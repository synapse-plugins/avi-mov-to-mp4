"""Upload action for avi-mov-to-mp4."""

from __future__ import annotations

from plugin.steps import ConvertVideoToMp4Step
from synapse_sdk.plugins.actions.upload import (
    DefaultUploadAction,
    UploadContext,
    UploadParams,
)
from synapse_sdk.plugins.steps import StepRegistry


class UploadAction(DefaultUploadAction[UploadParams]):
    """Upload action that converts AVI/MOV files to MP4 before upload.

    Extends the standard 8-step workflow by inserting a ConvertVideoToMp4Step
    after organize_files. The custom step converts AVI and MOV video files to
    H.264/AAC MP4 format using FFmpeg and replaces file references in-place.

    Extra params (via config.yaml ui_schema):
        - crf: Quality factor (0-51, lower = better quality, default: 23)
        - preset: Encoding preset (default: medium)
        - group_name: Group name to assign to all data units
    """

    action_name = 'upload'
    params_model = UploadParams

    def get_allowed_extensions(self) -> dict[str, list[str]] | None:
        """Restrict video files to AVI, MOV, and MP4 formats only."""
        return {
            'video': ['.avi', '.mov', '.mp4'],
        }

    def setup_steps(self, registry: StepRegistry[UploadContext]) -> None:
        super().setup_steps(registry)
        registry.insert_after('organize_files', ConvertVideoToMp4Step())
