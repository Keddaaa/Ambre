import { describe, expect, it } from "vitest";

import { decisionSources, explainDecision, safeHttpUrl } from "./justification";
import type { Decision } from "./types";

function decision(overrides: Partial<Decision> = {}): Decision {
  return {
    decision_id: "synthetic_d1",
    market_id: "synthetic_m1",
    market_question: "synthetic question",
    created_at: "2026-11-04T15:42:19Z",
    model_version: "base-rate-shrinkage-v0.1(w=0.9,n=22)",
    strategy: "VALUE_HUNTER",
    market_probability: 0.011,
    estimated_probability: 0.062,
    conservative_probability: 0.042,
    uncertainty: 0.02,
    expected_value: 11.6,
    ev_per_mana: 1.62,
    action: "YES",
    stake: 7.2,
    shares: 448.7,
    avg_price: 0.016,
    reasons: ["p=0.062", "q=0.011"],
    invalidation: ["market moves past p_cons", "estimate uncertainty exceeds edge"],
    source_ids: ["manifold:/v0/markets"],
    market_url: "https://manifold.markets/synthetic/market",
    research_sources: [],
    survival_state: "BALANCED",
    risk_reasons: ["edge after impact 1.6198 >= 0.03"],
    execution_status: "SIMULATED",
    actor: "AMBRE",
    outcome: null,
    payout: null,
    settled_at: null,
    ...overrides,
  };
}

describe("explainDecision: a placed bet", () => {
  it("speaks in the first person and states the stake", () => {
    expect(explainDecision(decision()).headline).toBe("J'ai parié YES pour M 7.20.");
  });
  it("says the market looks too low on a YES bet", () => {
    const lines = explainDecision(decision()).lines.join(" ");
    expect(lines).toContain("Le marché donne YES à 1.1 %, moi à 6.2 % ± 2.0 %");
    expect(lines).toContain("je le trouve trop bas");
  });
  it("says the market looks too high on a NO bet", () => {
    const d = decision({ action: "NO", market_probability: 0.9, estimated_probability: 0.86 });
    expect(explainDecision(d).lines.join(" ")).toContain("je le trouve trop haut");
  });
  it("gives the prudent estimate and the edge", () => {
    const lines = explainDecision(decision()).lines.join(" ");
    expect(lines).toContain("Même prudente, je retiens 4.2 %");
    expect(lines).toContain("1.62 mana par mana misé");
  });
  it("leaves out an edge that was not computed", () => {
    expect(explainDecision(decision({ ev_per_mana: null })).lines.join(" ")).not.toContain("par mana misé");
  });
  it("admits when only Manifold data was used", () => {
    expect(explainDecision(decision()).lines.join(" ")).toContain(
      "Mon estimation repose uniquement sur les données Manifold, sans aucune actualité.",
    );
  });
  it("does not claim that when another source was used", () => {
    const d = decision({ source_ids: ["manifold:/v0/markets", "gdelt:article"] });
    expect(explainDecision(d).lines.join(" ")).not.toContain("uniquement");
  });
  it("says what would change her mind", () => {
    expect(explainDecision(decision()).lines.join(" ")).toContain("Je changerais d'avis si");
  });
  it("reports a stake cut by the risk limits", () => {
    const d = decision({ risk_reasons: ["stake capped from 9.5000 to 7.2000"] });
    expect(explainDecision(d).lines.join(" ")).toContain("réduite de M 9.50 à M 7.20");
  });
});

describe("explainDecision: results", () => {
  it("reports a loss", () => {
    const d = decision({ outcome: "NO", payout: 0, settled_at: "2026-11-05T00:00:00Z" });
    expect(explainDecision(d).lines.at(-1)).toBe("Résultat : marché résolu NO, j'ai perdu M 7.20.");
  });
  it("reports a win", () => {
    const d = decision({ outcome: "YES", payout: 17.2, settled_at: "2026-11-05T00:00:00Z" });
    expect(explainDecision(d).lines.at(-1)).toBe("Résultat : marché résolu YES, j'ai gagné M 10.00.");
  });
  it("reports a refund", () => {
    const d = decision({ outcome: "CANCEL", payout: 7.2, settled_at: "2026-11-05T00:00:00Z" });
    expect(explainDecision(d).lines.at(-1)).toBe("Résultat : marché annulé, ma mise m'a été remboursée.");
  });
});

describe("explainDecision: no bet", () => {
  it("explains an abstention", () => {
    const e = explainDecision(
      decision({
        action: "NO_TRADE",
        execution_status: "NOT_EXECUTED",
        stake: 0,
        market_probability: 0.45,
        estimated_probability: 0.46,
        reasons: ["p=0.46", "no edge once the estimate is penalised by its uncertainty"],
        risk_reasons: [],
      }),
    );
    expect(e.headline).toBe("Je me suis abstenue.");
    expect(e.lines.join(" ")).toContain("je ne vois plus d'avantage");
  });
  it("explains a bet given up, without claiming it was placed", () => {
    const e = explainDecision(
      decision({
        execution_status: "REJECTED",
        risk_reasons: ["market synthetic_m1 already has an open position"],
      }),
    );
    expect(e.headline).toBe("J'ai renoncé à ce pari.");
    expect(e.lines).toContain("J'ai déjà une position ouverte sur ce marché.");
  });
  it("explains an edge eaten by the price impact", () => {
    const e = explainDecision(
      decision({ execution_status: "REJECTED", risk_reasons: ["edge after impact 0.0175 < 0.03"] }),
    );
    expect(e.lines.join(" ")).toContain("0.02 mana par mana misé, sous mon seuil de 0.03");
  });
  it("keeps a reason it cannot translate rather than dropping it", () => {
    const e = explainDecision(
      decision({ execution_status: "REJECTED", risk_reasons: ["fill simulation failed: boom"] }),
    );
    expect(e.lines).toContain("fill simulation failed: boom");
  });
});

describe("explainDecision: manual order", () => {
  it("does not take credit for someone else's order", () => {
    const e = explainDecision(decision({ actor: "USER" }));
    expect(e.headline).toBe("Ordre manuel (USER) : YES pour M 7.20.");
    expect(e.lines.join(" ")).not.toContain("moi à");
  });
});

describe("safeHttpUrl", () => {
  it("keeps http and https links", () => {
    expect(safeHttpUrl("https://example.test/a")).toBe("https://example.test/a");
    expect(safeHttpUrl("http://example.test/a")).toBe("http://example.test/a");
  });
  it("refuses anything a feed could use to run code", () => {
    expect(safeHttpUrl("javascript:alert(1)")).toBeNull();
    expect(safeHttpUrl("data:text/html,x")).toBeNull();
  });
  it("refuses a missing or malformed link", () => {
    expect(safeHttpUrl(null)).toBeNull();
    expect(safeHttpUrl("not a url")).toBeNull();
  });
});

describe("decisionSources", () => {
  const research = {
    provider_id: "spaceflight_news",
    title: "Synthetic headline",
    url: "https://example.test/a",
    published_at: "2026-11-03T10:00:00Z",
    linked_at: "2026-11-03T11:00:00Z",
    evidence_level: "unverified",
    usable_for_prediction: false,
  };

  it("always names the market she read", () => {
    expect(decisionSources(decision()).market).toEqual({
      label: "Page du marché sur Manifold",
      url: "https://manifold.markets/synthetic/market",
    });
  });
  it("says so when no research was linked", () => {
    expect(decisionSources(decision()).research).toEqual([]);
    expect(decisionSources(decision()).researchNote).toBe(
      "Aucune recherche n'était liée à ce marché quand j'ai décidé.",
    );
  });
  it("does not pretend research changed her estimate", () => {
    const s = decisionSources(decision({ research_sources: [research] }));
    expect(s.researchNote).toBe("Lu avant de décider, sans effet sur mon estimation :");
  });
  it("flags an unverified source", () => {
    const [line] = decisionSources(decision({ research_sources: [research] })).research;
    expect(line).toEqual({
      label: "Synthetic headline",
      url: "https://example.test/a",
      detail: "spaceflight_news · non vérifié",
    });
  });
  it("keeps a source without title or safe link as plain text", () => {
    const [line] = decisionSources(
      decision({ research_sources: [{ ...research, title: null, url: "javascript:alert(1)" }] }),
    ).research;
    expect(line.label).toBe("spaceflight_news");
    expect(line.url).toBeNull();
  });
});
