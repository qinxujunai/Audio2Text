export type CaptureStatus = "queued" | "processing" | "done" | "failed";

export type CaptureListItem = {
  id: string;
  status: CaptureStatus;
  title?: string | null;
  source_platform: string;
  content_type: string;
  primary_result_type?: string | null;
  created_at: string;
  updated_at: string;
  preview_text: string;
};

export type CaptureListResponse = {
  count: number;
  items: CaptureListItem[];
};

export type ResultViews = {
  primary: string;
  transcript: string;
  subtitle: string;
  article: string;
  notes: string;
  timeline: string;
  markdown: string;
  json: string;
  meta: string;
  ocr: string;
};

export type ContentFactItem = {
  key: string;
  label: string;
  value: string;
  href?: string | null;
};

export type Artifact = {
  type: string;
  label: string;
  download_url: string;
  mime_type: string;
  size_bytes?: number | null;
  status: "pending" | "ready" | "failed";
  optional: boolean;
};

export type CaptureSourceImage = {
  index: number;
  image_url: string;
  live_photo_video_url?: string | null;
};

export type CaptureEnvelope = {
  capture: {
    id: string;
    status: CaptureStatus;
    result_state: "processing" | "text_ready" | "complete" | "failed";
    input_kind: "url" | "file";
    current_stage: string;
    progress_percent: number;
    progress_detail: string;
    title?: string | null;
    created_at: string;
    updated_at: string;
    started_at?: string | null;
    finished_at?: string | null;
    error_stage?: string | null;
    error_message?: string | null;
    retry_count: number;
    retryable: boolean;
    failure_reason_code?: string | null;
    asset_preparation_pending: boolean;
  };
  source: {
    platform: string;
    media_kind: string;
    canonical_url?: string | null;
    title?: string | null;
    author?: string | null;
    duration_seconds?: number | null;
    thumbnail_url?: string | null;
    description?: string | null;
    detected_urls: string[];
    image_urls: string[];
    images: CaptureSourceImage[];
    warnings: string[];
    extractor_used: string;
    fallback_used: boolean;
  };
  quality: {
    primary_result_type: string;
    confidence: "high" | "medium" | "low";
    completeness: "full" | "substantial" | "partial";
    cache_hit: boolean;
    transcript_status: "skipped" | "subtitle" | "transcribed" | "failed";
    text_source:
      "none" | "subtitle" | "article" | "notes" | "transcript" | "ocr";
    subtitle_source: "none" | "manual" | "auto" | "translated";
    selected_language: string;
    result_notice: string;
  };
  result: {
    primary_text: string;
    extraction_path: string[];
    views: ResultViews;
    content_facts: ContentFactItem[];
  };
  artifacts: Artifact[];
};

export type CreateCaptureResponse = {
  capture_id: string;
  status: CaptureStatus;
  reused: boolean;
  cache_hit: boolean;
  source_platform: string;
  primary_result_type: string;
  input_warning?: string | null;
};

export type ConfigResponse = {
  product_name: string;
  product_feature_name: string;
  product_summary: string;
  product_slogan: string;
  url_input_platform_hints: Record<string, string>;
  capture_history_limit: number;
  supported_extensions: string[];
  max_upload_size_mb: number;
  free_duration_minutes?: number;
  deployment_mode?: string;
  public_preview_mode?: boolean;
  transcription_available?: boolean;
  transcription_provider?: string;
  runtime_target?: string;
  capabilities?: Record<string, boolean>;
};

export type RuntimePack = {
  id: string;
  version: string;
  size_bytes: number;
  required: boolean;
  installed: boolean;
};
