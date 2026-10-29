"use client";

import { useState } from "react";

import { StatusBadge } from "@/components/ui";
import { useFilters } from "@/lib/filters";
import { fmtBRLCompact, fmtCompetencia, fmtPct } from "@/lib/format";
import { useApi } from "@/lib/useApi";

interface MesCompletude {
  competencia: string;
  idade_meses: number;
  status: "preliminar" | "fechada";
  completude_pct: number;
  despesa_liquida_observada: number;
  despesa_liquida_estimada_final: number;
  ibnr_estimado: number;
  sinistralidade_observada: number | null;
  sinistralidade_projetada: number | null;
}

export interface Completude {
  disponivel: boolean;
  motivo?: string;
  corte: string | null;
  metodo: string;
  cobertura_data_apresentacao_pct: number;
  meses: MesCompletude[];
  selecionada: MesCompletude | null;
  limitacoes?: string[];
}

/**
 * Item 1 — avisa quando a competência ainda está PRELIMINAR (contas a chegar) e mostra a
 * estimativa final. Competência fechada: só um selo discreto.
 */
export function CompletudeBanner() {
  const f = useFilters();
  const { data } = useApi<Completude>(f.competencia ? `/executive/completude?competencia=${f.competencia}` : null);
  const [aberto, setAberto] = useState(false);
  if (!data) return null;
  if (!data.disponivel) {
    return (
      <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-2 text-xs text-slate-500">
        Completude da competência indisponível: {data.motivo}
      </div>
    );
  }
  const s = data.selecionada;
  if (!s) return null;
  if (s.status === "fechada") {
    return (
      <div className="flex items-center gap-2 text-xs text-slate-500">
        <StatusBadge tone="success">Competência fechada</StatusBadge>
        <span>{fmtPct(s.completude_pct, 0)} das contas esperadas já recebidas.</span>
      </div>
    );
  }
  const recentes = data.meses.filter((m) => m.status === "preliminar");
  return (
    <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <StatusBadge tone="warning">Competência Preliminar</StatusBadge>
        <span>
          <strong>{fmtPct(s.completude_pct, 0)}</strong> das contas de {fmtCompetencia(s.competencia)} já chegaram.
          Estimativa final da despesa: <strong>{fmtBRLCompact(s.despesa_liquida_estimada_final)}</strong>{" "}
          (+{fmtBRLCompact(s.ibnr_estimado)} a chegar)
          {s.sinistralidade_projetada != null && (
            <>
              {" "}· sinistralidade projetada <strong>{fmtPct(s.sinistralidade_projetada)}</strong>
              {s.sinistralidade_observada != null && <> (hoje {fmtPct(s.sinistralidade_observada)})</>}
            </>
          )}
          .
        </span>
        <button
          type="button"
          onClick={() => setAberto((v) => !v)}
          className="text-xs font-medium underline decoration-dotted underline-offset-2"
        >
          {aberto ? "Ocultar" : "Como estimamos"}
        </button>
      </div>
      {aberto && (
        <div className="mt-3 space-y-2 rounded-lg bg-white/70 p-3 text-xs text-slate-700">
          <p>
            Método: {data.metodo}. Corte: contas apresentadas até {data.corte ? fmtCompetencia(data.corte) : "—"}.
            Observado = FATO; estimativa final e sinistralidade projetada = ESTIMATIVA.
          </p>
          <table className="w-full">
            <thead>
              <tr className="text-left text-slate-400">
                <th className="py-1">Competência</th>
                <th className="py-1 text-right">Recebido</th>
                <th className="py-1 text-right">Observado</th>
                <th className="py-1 text-right">Estimativa final</th>
                <th className="py-1 text-right">A chegar</th>
              </tr>
            </thead>
            <tbody>
              {recentes.map((m) => (
                <tr key={m.competencia} className="border-t border-slate-100">
                  <td className="py-1">{fmtCompetencia(m.competencia)}</td>
                  <td className="py-1 text-right tabular-nums">{fmtPct(m.completude_pct, 0)}</td>
                  <td className="py-1 text-right tabular-nums">{fmtBRLCompact(m.despesa_liquida_observada)}</td>
                  <td className="py-1 text-right tabular-nums">{fmtBRLCompact(m.despesa_liquida_estimada_final)}</td>
                  <td className="py-1 text-right tabular-nums">{fmtBRLCompact(m.ibnr_estimado)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <ul className="list-disc pl-4 text-slate-500">
            {(data.limitacoes ?? []).map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
