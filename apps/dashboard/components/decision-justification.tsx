import { type CitedSource, decisionSources, explainDecision } from "@/lib/justification";
import type { Decision } from "@/lib/types";

function Citation({ source }: { source: CitedSource }) {
  return source.url === null ? (
    <span>{source.label}</span>
  ) : (
    <a className="underline hover:text-foreground" href={source.url} target="_blank" rel="noopener noreferrer">
      {source.label}
    </a>
  );
}

/** Ambre's own explanation of a decision and her sources, shown right under it. */
export function DecisionJustification({ decision }: { decision: Decision }) {
  const { headline, lines } = explainDecision(decision);
  const { market, researchNote, research } = decisionSources(decision);
  return (
    <div className="flex flex-col gap-1 text-sm">
      <p className="font-medium">{headline}</p>
      {lines.length > 0 ? (
        <ul className="flex flex-col gap-0.5 text-muted-foreground">
          {lines.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      ) : null}
      <div className="mt-1 flex flex-col gap-0.5 text-xs text-muted-foreground">
        <p>
          <span className="font-medium">Mes sources : </span>
          <Citation source={market} />
        </p>
        <p>{researchNote}</p>
        {research.length > 0 ? (
          <ul className="flex flex-col gap-0.5">
            {research.map((source, i) => (
              <li key={`${source.label}-${i}`}>
                <Citation source={source} /> <span>({source.detail})</span>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </div>
  );
}
