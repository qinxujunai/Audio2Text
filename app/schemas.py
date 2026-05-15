from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


CaptureStatus = Literal["queued", "processing", "done", "failed"]
InputType = Literal["url", "file"]
ContentType = Literal["video", "audio", "article", "image_article", "webpage", "unknown"]
ResultOrigin = Literal["subtitle", "transcript", "article", "notes", "ocr"]
SegmentKind = Literal["text", "timed_text", "note", "ocr"]
ConfidenceLevel = Literal["high", "medium", "low"]
CompletenessLevel = Literal["full", "substantial", "partial"]
TranscriptStatus = Literal["skipped", "subtitle", "transcribed", "failed"]
TextSource = Literal["none", "subtitle", "article", "notes", "transcript", "ocr"]
SubtitleSource = Literal["none", "manual", "auto", "translated"]


class ArtifactModel(BaseModel):
    type: str
    label: str
    path: str
    download_url: str
    mime_type: str = "application/octet-stream"
    size_bytes: int | None = None


class ArtifactPayloadModel(BaseModel):
    type: str
    label: str
    download_url: str
    mime_type: str = "application/octet-stream"
    size_bytes: int | None = None


class CaptureSourceImagePayloadModel(BaseModel):
    index: int
    image_url: str
    live_photo_video_url: str | None = None


class ContentFactItemModel(BaseModel):
    key: str
    label: str
    value: str
    href: str | None = None


class TraceEventModel(BaseModel):
    at: str
    stage: str
    level: Literal["info", "warning", "error"] = "info"
    message: str
    provider: str | None = None
    detail: str | None = None


class ResultSegmentModel(BaseModel):
    kind: SegmentKind
    origin: ResultOrigin
    text: str
    start_sec: float | None = None
    end_sec: float | None = None


class ResultViewsModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    primary: str = ""
    transcript: str = ""
    subtitle: str = ""
    article: str = ""
    notes: str = ""
    timeline: str = ""
    markdown: str = ""
    json_view: str = Field(default="", alias="json", serialization_alias="json")
    meta: str = ""
    ocr: str = ""


class SourceMetaModel(BaseModel):
    platform: str
    content_type: str
    canonical_url: str | None = None
    source_item_id: str | None = None
    raw_input_text: str | None = None
    detected_urls: list[str] = Field(default_factory=list)
    selection_reason: str | None = None
    author: str | None = None
    published_at: str | None = None
    language: str | None = None
    thumbnail_url: str | None = None
    duration_seconds: float | None = None
    description: str | None = None
    image_urls: list[str] = Field(default_factory=list)
    live_photo_video_urls: list[str] = Field(default_factory=list)
    strategy: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    extractor_used: str = ""
    fallback_used: bool = False
    fallback_chain: list[str] = Field(default_factory=list)


class ResultDocumentModel(BaseModel):
    primary_text: str = ""
    primary_result_type: str = ""
    source_item_id: str = ""
    cache_hit: bool = False
    extraction_path: list[str] = Field(default_factory=list)
    confidence: ConfidenceLevel = "low"
    completeness: CompletenessLevel = "partial"
    transcript_status: TranscriptStatus = "skipped"
    text_source: TextSource = "none"
    subtitle_source: SubtitleSource = "none"
    selected_language: str = ""
    result_notice: str = ""
    transcript_text: str = ""
    timeline_text: str = ""
    markdown_text: str = ""
    meta_text: str = ""
    subtitle_text: str = ""
    article_text: str = ""
    notes_text: str = ""
    ocr_text: str = ""
    json_text: str = ""
    segments: list[ResultSegmentModel] = Field(default_factory=list)
    views: ResultViewsModel = Field(default_factory=ResultViewsModel)
    content_facts: list[ContentFactItemModel] = Field(default_factory=list)
    artifacts: list[ArtifactModel] = Field(default_factory=list)


class ProcessingStateModel(BaseModel):
    current_stage: str = "queued"
    progress_percent: int = 0
    progress_detail: str = ""
    completed_stages: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    extraction_strategy: list[str] = Field(default_factory=list)
    retry_count: int = 0
    failure_reason_code: str | None = None
    retryable: bool = False
    trace_events: list[TraceEventModel] = Field(default_factory=list)


class CaptureModel(BaseModel):
    id: str
    input_type: InputType
    status: CaptureStatus
    title: str | None = None
    source_platform: str
    content_type: str
    url: str | None = None
    file_name: str | None = None
    storage_path: str | None = None
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error_stage: str | None = None
    error_message: str | None = None
    asset_preparation_pending: bool = False
    client_ip: str | None = None
    source: SourceMetaModel
    processing: ProcessingStateModel
    result: ResultDocumentModel | None = None


class CaptureListItemModel(BaseModel):
    id: str
    status: CaptureStatus
    title: str | None = None
    source_platform: str
    content_type: str
    primary_result_type: str | None = None
    created_at: str
    updated_at: str
    preview_text: str = ""


class CaptureListResponse(BaseModel):
    count: int
    items: list[CaptureListItemModel]


class CaptureCreateUrlRequest(BaseModel):
    url: str | None = None
    text: str | None = None


class CaptureStatePayloadModel(BaseModel):
    id: str
    status: CaptureStatus
    input_kind: InputType
    current_stage: str = "queued"
    progress_percent: int = 0
    progress_detail: str = ""
    title: str | None = None
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error_stage: str | None = None
    error_message: str | None = None
    retry_count: int = 0
    retryable: bool = False
    asset_preparation_pending: bool = False


class CaptureSourcePayloadModel(BaseModel):
    platform: str
    media_kind: str
    canonical_url: str | None = None
    title: str | None = None
    author: str | None = None
    duration_seconds: float | None = None
    thumbnail_url: str | None = None
    description: str | None = None
    detected_urls: list[str] = Field(default_factory=list)
    image_urls: list[str] = Field(default_factory=list)
    images: list[CaptureSourceImagePayloadModel] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    extractor_used: str = ""
    fallback_used: bool = False


class CaptureQualityPayloadModel(BaseModel):
    primary_result_type: str = ""
    confidence: ConfidenceLevel = "low"
    completeness: CompletenessLevel = "partial"
    cache_hit: bool = False
    transcript_status: TranscriptStatus = "skipped"
    text_source: TextSource = "none"
    subtitle_source: SubtitleSource = "none"
    selected_language: str = ""
    result_notice: str = ""


class CaptureResultPayloadModel(BaseModel):
    primary_text: str = ""
    extraction_path: list[str] = Field(default_factory=list)
    segments: list[ResultSegmentModel] = Field(default_factory=list)
    views: ResultViewsModel = Field(default_factory=ResultViewsModel)
    content_facts: list[ContentFactItemModel] = Field(default_factory=list)


class CaptureEnvelopeModel(BaseModel):
    capture: CaptureStatePayloadModel
    source: CaptureSourcePayloadModel
    quality: CaptureQualityPayloadModel
    result: CaptureResultPayloadModel
    artifacts: list[ArtifactPayloadModel] = Field(default_factory=list)


class CaptureEventEnvelopeModel(BaseModel):
    event: str
    payload: CaptureEnvelopeModel
