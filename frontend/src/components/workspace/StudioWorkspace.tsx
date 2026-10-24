import { useEffect, useRef, useState } from "react";
import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ErrorState } from "@/components/preview/ErrorState";
import { LoadingState } from "@/components/preview/LoadingState";
import { ResultView } from "@/components/preview/ResultView";
import { AdaptiveForm } from "@/components/workspace/AdaptiveForm";
import { LessonLibraryDrawer } from "@/components/workspace/LessonLibraryDrawer";
import {
  StudioEmptyState,
  type StudioPreset,
} from "@/components/workspace/StudioEmptyState";
import { api, ApiProblemError } from "@/lib/api";
import { generatedContentSchema } from "@/schemas/content";
import type {
  GeneratedContent,
  SubjectLiteral,
  TaskType,
  TeacherRequest,
} from "@/types/content";

/*
 * Abort budget per task type. These stay ABOVE the server's own ceiling so the
 * backend always answers first; the user can also cancel at any time.
 */
const TIMEOUT_MS: Record<TaskType, number> = {
  worksheet: 130_000,
  powerpoint: 130_000,
  booklet: 260_000,
};

type Canvas =
  | { name: "empty" }
  | { name: "loading"; request: TeacherRequest }
  | { name: "error"; request: TeacherRequest; error: unknown }
  | { name: "result"; content: GeneratedContent; uuid: string | null };

type RailTab = "new" | "library";

export function StudioWorkspace({
  allowedSubjects,
  allowedTaskTypes,
  maintenanceMode,
  onGenerated,
}: {
  allowedSubjects: readonly SubjectLiteral[];
  allowedTaskTypes: readonly TaskType[];
  maintenanceMode: boolean;
  /** Reports each billable (non-cache) generation for the quota indicator. */
  onGenerated: () => void;
}) {
  const [canvas, setCanvas] = useState<Canvas>({ name: "empty" });
  const [railOpen, setRailOpen] = useState(true);
  const [railTab, setRailTab] = useState<RailTab>("new");
  const [preset, setPreset] = useState<StudioPreset | null>(null);
  const [libraryRefresh, setLibraryRefresh] = useState(0);
  const controllerRef = useRef<AbortController | null>(null);
  const lastResult = useRef<Canvas | null>(null);

  useEffect(() => () => controllerRef.current?.abort(), []);

  async function generate(request: TeacherRequest) {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setCanvas({ name: "loading", request });
    try {
      const result = await api.generate(request, {
        signal: AbortSignal.any([
          controller.signal,
          AbortSignal.timeout(TIMEOUT_MS[request.task_type]),
        ]),
      });
      const parsed = generatedContentSchema.safeParse(result.body);
      if (!parsed.success) {
        throw new ApiProblemError({
          status: 502,
          title: "Unexpected response",
          detail:
            "The service sent back something unexpected. Please try again.",
        });
      }
      const next: Canvas = {
        name: "result",
        content: parsed.data as GeneratedContent,
        uuid: result.generationUuid,
      };
      lastResult.current = next;
      setCanvas(next);
      if (!result.cacheHit) onGenerated();
      setLibraryRefresh((n) => n + 1);
    } catch (err) {
      // A user-initiated cancel has already restored the canvas.
      if (controller.signal.aborted) return;
      const timedOut =
        err instanceof DOMException && err.name === "TimeoutError";
      setCanvas({
        name: "error",
        request,
        error: timedOut
          ? new ApiProblemError({
              status: 504,
              title: "Generation timed out",
              detail:
                "The AI is taking too long to answer. Try again in a moment.",
            })
          : err,
      });
    } finally {
      if (controllerRef.current === controller) controllerRef.current = null;
    }
  }

  function cancelGeneration() {
    controllerRef.current?.abort();
    controllerRef.current = null;
    setCanvas(lastResult.current ?? { name: "empty" });
  }

  const serverErrors =
    canvas.name === "error" && canvas.error instanceof ApiProblemError
      ? canvas.error.invalidParams
      : undefined;
  const generating = canvas.name === "loading";

  return (
    <div
      className={`grid min-h-0 flex-1 gap-4 p-4 ${
        railOpen
          ? "lg:grid-cols-[360px_minmax(0,1fr)]"
          : "lg:grid-cols-[44px_minmax(0,1fr)]"
      }`}
    >
      <aside
        data-app-chrome
        className="no-print flex min-w-0 flex-col gap-3 lg:sticky lg:top-4 lg:max-h-[calc(100vh-6rem)] lg:self-start"
        aria-label="Studio rail"
      >
        <Button
          variant="ghost"
          size="sm"
          className="self-start"
          onClick={() => setRailOpen((o) => !o)}
          aria-expanded={railOpen}
          aria-label={railOpen ? "Collapse studio rail" : "Expand studio rail"}
        >
          {railOpen ? (
            <>
              <PanelLeftClose className="size-4" strokeWidth={1.5} />
              Studio rail
            </>
          ) : (
            <PanelLeftOpen className="size-4" strokeWidth={1.5} />
          )}
        </Button>
        {railOpen && (
          <Tabs
            value={railTab}
            onValueChange={(v) => setRailTab(v as RailTab)}
            className="flex min-h-0 flex-col gap-3 overflow-y-auto rounded-lg border border-border bg-card p-3"
          >
            <TabsList className="w-full">
              <TabsTrigger value="new">New Resource</TabsTrigger>
              <TabsTrigger value="library">Saved Library</TabsTrigger>
            </TabsList>
            <TabsContent value="new">
              <AdaptiveForm
                allowedSubjects={allowedSubjects}
                allowedTaskTypes={allowedTaskTypes}
                onGenerate={generate}
                serverErrors={serverErrors}
                disabled={generating || maintenanceMode}
                preset={preset}
              />
            </TabsContent>
            <TabsContent value="library">
              <LessonLibraryDrawer
                allowedSubjects={allowedSubjects}
                allowedTaskTypes={allowedTaskTypes}
                activeUuid={canvas.name === "result" ? canvas.uuid : null}
                refreshKey={libraryRefresh}
                onOpenLesson={(detail) => {
                  const next: Canvas = {
                    name: "result",
                    content: detail.content,
                    uuid: detail.uuid,
                  };
                  lastResult.current = next;
                  setCanvas(next);
                }}
                onDeleted={(uuid) => {
                  if (canvas.name === "result" && canvas.uuid === uuid) {
                    lastResult.current = null;
                    setCanvas({ name: "empty" });
                  }
                }}
              />
            </TabsContent>
          </Tabs>
        )}
      </aside>

      <section className="min-w-0" aria-label="Document canvas">
        {canvas.name === "empty" && (
          <StudioEmptyState
            allowedSubjects={allowedSubjects}
            onPreset={(p) => {
              setPreset(p);
              setRailOpen(true);
              setRailTab("new");
            }}
            onOpenLibrary={() => {
              setRailOpen(true);
              setRailTab("library");
            }}
          />
        )}
        {canvas.name === "loading" && (
          <LoadingState request={canvas.request} onCancel={cancelGeneration} />
        )}
        {canvas.name === "error" && (
          <ErrorState
            error={canvas.error}
            onRetry={() => generate(canvas.request)}
            onDismiss={() => setCanvas(lastResult.current ?? { name: "empty" })}
          />
        )}
        {canvas.name === "result" && (
          <ResultView
            key={canvas.uuid ?? canvas.content.title}
            content={canvas.content}
            lessonUuid={canvas.uuid}
            onContentChange={(content) => {
              const next: Canvas = {
                name: "result",
                content,
                uuid: canvas.uuid,
              };
              lastResult.current = next;
              setCanvas(next);
            }}
          />
        )}
      </section>
    </div>
  );
}
