import { useState, useEffect, useRef, useCallback } from "react";
import type { LeaderSource } from "../types.ts";
import { shortId, fmt } from "../lib/format.ts";

interface Props {
  leader: LeaderSource;
}

export default function LeaderCode({ leader }: Props) {
  const [highlightedHtml, setHighlightedHtml] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const shikiRef = useRef<ReturnType<typeof import("shiki")["createHighlighter"]> | null>(null);

  const code =
    leader.files.length > 0 ? leader.files[0].content.trim() : "";
  const filePath = leader.files.length > 0 ? leader.files[0].path : "unknown";

  useEffect(() => {
    if (!code) return;

    let cancelled = false;

    (async () => {
      try {
        const { createHighlighter } = await import("shiki");
        if (cancelled) return;

        if (!shikiRef.current) {
          shikiRef.current = createHighlighter({
            themes: ["vitesse-dark"],
            langs: ["cpp"],
          });
        }
        const highlighter = await shikiRef.current;
        if (cancelled) return;

        const html = highlighter.codeToHtml(code, {
          lang: "cpp",
          theme: "vitesse-dark",
        });
        if (!cancelled) setHighlightedHtml(html);
      } catch {
        if (!cancelled) setHighlightedHtml(null);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [code]);

  const handleCopy = useCallback(() => {
    if (!code) return;
    navigator.clipboard.writeText(code).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }, [code]);

  return (
    <div className="bg-surface-50 backdrop-blur-xl border border-surface-200 rounded-xl overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-surface-200">
        <div className="flex items-center gap-3">
          <h3 className="text-sm font-semibold text-cuda">
            Full Leader Kernel
          </h3>
          <span className="text-[11px] font-mono text-gray-500">{filePath}</span>
        </div>
        <div className="flex items-center gap-4 text-xs">
          <span className="text-gray-500">
            candidate{" "}
            <span className="font-mono text-gray-300">
              {shortId(leader.candidate_id)}
            </span>
          </span>
          <span className="text-gray-500">
            fitness{" "}
            <span className="font-bold text-accent tabular-nums">
              {fmt(leader.fitness, 2)}
            </span>
          </span>
          {leader.stage && (
            <span className="rounded-full bg-accent/10 border border-accent/20 px-2 py-0.5 text-[10px] text-accent">
              {leader.stage}
            </span>
          )}
          {code && (
            <button
              onClick={handleCopy}
              className="ml-2 px-2.5 py-1 rounded-md text-[11px] font-medium border transition-colors cursor-pointer
                border-surface-200 text-gray-400 hover:text-gray-200 hover:border-gray-400
                bg-surface-50 hover:bg-surface-100"
              title="Copy kernel source"
            >
              {copied ? "Copied!" : "Copy"}
            </button>
          )}
        </div>
      </div>

      {leader.hypothesis && (
        <div className="px-4 py-2 border-b border-surface-200 text-xs text-gray-400 italic">
          Hypothesis: {leader.hypothesis}
        </div>
      )}

      <div className="code-scroll overflow-auto max-h-[500px]">
        {highlightedHtml ? (
          <div
            className="text-sm [&_pre]:!bg-transparent [&_pre]:p-4 [&_pre]:m-0 [&_code]:text-[13px] [&_code]:leading-relaxed"
            dangerouslySetInnerHTML={{ __html: highlightedHtml }}
          />
        ) : code ? (
          <pre className="p-4 text-[13px] leading-relaxed text-gray-300 font-mono whitespace-pre">
            {code}
          </pre>
        ) : (
          <div className="p-4 text-sm text-gray-500">
            No source code available
          </div>
        )}
      </div>

      {leader.origin && Object.keys(leader.origin).length > 0 && (
        <div className="px-4 py-2 border-t border-surface-200 flex items-center gap-3 text-[11px] text-gray-500">
          <span>
            agent: <span className="text-gray-400">{(leader.origin as Record<string, string>).agent_id ?? "?"}</span>
          </span>
          <span>
            island: <span className="text-gray-400">{(leader.origin as Record<string, string>).island_id ?? "?"}</span>
          </span>
          <span>
            op: <span className="text-gray-400">{(leader.origin as Record<string, string>).operation ?? "?"}</span>
          </span>
        </div>
      )}
    </div>
  );
}
