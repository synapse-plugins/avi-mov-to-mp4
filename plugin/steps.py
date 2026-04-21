"""Custom steps for avi-mov-to-mp4 upload plugin."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import ffmpeg

from synapse_sdk.plugins.actions.upload.context import UploadContext
from synapse_sdk.plugins.steps import BaseStep, StepResult


class ConvertVideoToMp4Step(BaseStep[UploadContext]):
    """Convert AVI/MOV video files to MP4 format using FFmpeg.

    Each AVI or MOV file is converted to H.264/AAC MP4 in a temporary directory.
    The original file reference in organized_files is replaced with the converted MP4.
    Files that are already MP4 or non-video are passed through unchanged.

    Reads extra_params from context:
        - crf (int): Quality factor, lower = better quality. Default: 23.
        - preset (str): Encoding speed/compression tradeoff. Default: 'medium'.
        - group_name (str | None): Group name to assign to all data units.
    """

    CONVERTIBLE_EXTENSIONS = {'.avi', '.mov'}

    @property
    def name(self) -> str:
        return 'convert_video_to_mp4'

    @property
    def progress_weight(self) -> float:
        return 0.2

    def can_skip(self, context: UploadContext) -> bool:
        """Skip if no AVI/MOV files found in organized_files."""
        for file_group in context.organized_files:
            for file_path in file_group.get('files', {}).values():
                if isinstance(file_path, list):
                    file_path = file_path[0] if file_path else None
                if file_path and Path(file_path).suffix.lower() in self.CONVERTIBLE_EXTENSIONS:
                    return False
        return True

    def execute(self, context: UploadContext) -> StepResult:
        self._validate_ffmpeg()

        extra = context.params.get('extra_params') or {}
        crf = int(extra.get('crf', 23))
        preset = extra.get('preset', 'medium')
        group_name = extra.get('group_name')

        temp_dir = self._create_temp_directory(context)
        processed_files: list[dict[str, Any]] = []
        total_converted = 0

        try:
            for file_group in context.organized_files:
                files_dict = file_group.get('files', {})
                meta = file_group.get('meta', {})

                converted_files: dict[str, Any] = {}
                skip_group = False

                for spec_name, file_path in files_dict.items():
                    if isinstance(file_path, list):
                        file_path = file_path[0] if file_path else None
                    if file_path is None:
                        converted_files[spec_name] = file_path
                        continue

                    file_path = Path(file_path)
                    if file_path.suffix.lower() not in self.CONVERTIBLE_EXTENSIONS:
                        converted_files[spec_name] = file_path
                        continue

                    converted_path = self._convert_to_mp4(
                        file_path, temp_dir, crf, preset, context,
                    )

                    if converted_path is None:
                        context.log(
                            'video_conversion_failed',
                            {'file': file_path.name, 'reason': 'ffmpeg conversion error'},
                        )
                        skip_group = True
                        break

                    converted_files[spec_name] = converted_path
                    total_converted += 1

                if skip_group:
                    continue

                entry: dict[str, Any] = {
                    **file_group,
                    'files': converted_files,
                    'meta': {
                        **meta,
                        'origin_file_format': next(
                            (
                                Path(fp).suffix.lstrip('.').lower()
                                for fp in files_dict.values()
                                if fp and Path(fp).suffix.lower() in self.CONVERTIBLE_EXTENSIONS
                            ),
                            meta.get('origin_file_format', ''),
                        ),
                    },
                }
                if group_name:
                    entry['groups'] = [group_name]

                processed_files.append(entry)

            context.organized_files = processed_files

            context.params['cleanup_temp'] = True
            context.params['temp_path'] = str(temp_dir)

            context.log(
                'video_conversion_complete',
                {'total_converted': total_converted, 'total_entries': len(processed_files)},
            )

            return StepResult(
                success=True,
                data={'videos_converted': total_converted},
                rollback_data={'temp_dir': str(temp_dir)},
            )

        except Exception as e:
            return StepResult(success=False, error=f'Video conversion failed: {e}')

    def rollback(self, context: UploadContext, result: StepResult) -> None:
        temp_dir = result.rollback_data.get('temp_dir')
        if temp_dir and Path(temp_dir).exists():
            shutil.rmtree(temp_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _validate_ffmpeg(self) -> None:
        """Validate that FFmpeg is installed and available."""
        if not shutil.which('ffmpeg'):
            msg = 'FFmpeg is not installed or not found in PATH.'
            raise RuntimeError(msg)

    def _create_temp_directory(self, context: UploadContext) -> Path:
        base = context.pathlib_cwd if context.pathlib_cwd else Path(os.getcwd())
        temp_dir = base / 'temp_converted_videos'
        temp_dir.mkdir(parents=True, exist_ok=True)
        return temp_dir

    def _convert_to_mp4(
        self,
        input_path: Path,
        output_dir: Path,
        crf: int,
        preset: str,
        context: UploadContext,
    ) -> Path | None:
        """Convert a single video file to MP4 using FFmpeg.

        Returns:
            Path to the converted MP4 file, or None on failure.
        """
        output_path = output_dir / f'{input_path.stem}.mp4'

        input_size_mb = input_path.stat().st_size / (1024 * 1024)
        context.log(
            'video_conversion_start',
            {'file': input_path.name, 'size_mb': f'{input_size_mb:.2f}'},
        )

        try:
            (
                ffmpeg.input(str(input_path))
                .output(
                    str(output_path),
                    vcodec='libx264',
                    acodec='aac',
                    crf=crf,
                    preset=preset,
                    movflags='faststart',
                    **{'max_muxing_queue_size': '1024'},
                )
                .overwrite_output()
                .run(capture_stdout=True, capture_stderr=True)
            )
        except ffmpeg.Error as e:
            stderr = e.stderr.decode() if e.stderr else str(e)
            context.log(
                'video_conversion_ffmpeg_error',
                {'file': input_path.name, 'error': stderr[:500]},
            )
            if output_path.exists():
                output_path.unlink()
            return None
        except Exception as e:
            context.log(
                'video_conversion_error',
                {'file': input_path.name, 'error': str(e)[:500]},
            )
            if output_path.exists():
                output_path.unlink()
            return None

        if not output_path.exists():
            return None

        output_size_mb = output_path.stat().st_size / (1024 * 1024)
        context.log(
            'video_conversion_done',
            {'file': input_path.name, 'output': output_path.name, 'output_size_mb': f'{output_size_mb:.2f}'},
        )
        return output_path
