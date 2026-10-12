export type SavedArtifact = {
  file_name: string;
};

export function isDesktopApp() {
  return "__TAURI_INTERNALS__" in window;
}

async function invokeDesktop<T>(
  command: string,
  captureId: string,
  artifactType: string,
) {
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<T>(command, { captureId, artifactType });
}

export function saveDesktopArtifact(captureId: string, artifactType: string) {
  return invokeDesktop<SavedArtifact>("save_artifact", captureId, artifactType);
}

export function saveDesktopArtifactAs(captureId: string, artifactType: string) {
  return invokeDesktop<SavedArtifact | null>(
    "save_artifact_as",
    captureId,
    artifactType,
  );
}

export function openSavedArtifact(captureId: string, artifactType: string) {
  return invokeDesktop<void>("open_saved_artifact", captureId, artifactType);
}

export function revealSavedArtifact(captureId: string, artifactType: string) {
  return invokeDesktop<void>("reveal_saved_artifact", captureId, artifactType);
}

export async function openCaptureSource(captureId: string) {
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<void>("open_capture_source", { captureId });
}
