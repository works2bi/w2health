"use client";

import { useState } from "react";

import { Card, DataState, Select, StatusBadge } from "@/components/ui";
import { useFilters } from "@/lib/filters";
import { fmtBRL, fmtCompetencia, fmtPct } from "@/lib/format";
import { useApi } from "@/lib/useApi";

interface Ref {
  p25: number;
  mediana: number;
  p75: number;
  media_ponderada: number | null;
  n_operadoras: number;
  periodo: string;
}

interface Item {
  indicador: "sinistralidade_pct" | "despesa_pmpm";
  rotulo: string;
  operadora: number | null;
  referencia: Ref | null;
  posicao: string | null;
  posicao_rotulo: string | null;
  diferenca_mediana: number | null;
}

export interface Benchmark {
  segmento: string;
  segmento_rotulo: string;
  segmentos: { codigo: string; rotulo: string }[];
  fonte: string | null;
  ilustrativo: boolean;
  disponivel: boolean;
  aviso: string | null;
  janela: { inicio: string | null; fim: string | null; meses: number };
  itens: Item[];
  limitacoes: string[];
}

const fmtValor = (ind: string, v: number | null) => (ind === "sinistralidade_pct" ? fmtPct(v) : fmtBRL(v, true));
const TOM: Record<string, "success" | "info" | "warning" | "danger"> = {
  abaixo_p25: "success",
  p25_mediana: "info",
  mediana_p75: "warning",
  acima_p75: "danger",
};

/** Régua p25–p75 com a mediana e o ponto da operadora. */
export function Regua({ item }: { item: Item }) {
  const r = item.referencia;
  if (!r || item.operadora == null) return null;
  const min = Math.min(r.p25, item.operadora) * 0.9;
  const max = Math.max(r.p75, item.operadora) * 1.1;
  const x = (v: number) => `${((v - min) / (max - min)) * 100}%`;
  return (
    <div className="relative mt-3 h-8" aria-hidden>
      <div className="absolute top-3 h-2 w-full rounded bg-slate-100" />
      <div className="absolute top-3 h-2 rounded bg-brand-100" style={{ left: x(r.p25), width: `calc(${x(r.p75)} - ${x(r.p25)})` }} />
      <div className="absolute top-2 h-4 w-0.5 bg-brand-600" style={{ left: x(r.mediana) }} />
      <div
        className="absolute top-1.5 h-5 w-5 -translate-x-1/2 rounded-full border-2 border-white bg-gold-500 shadow"
        style={{ left: x(item.operadora) }}
      />
    </div>
  );
}

/** Item 7 — operadora × mercado (dados abertos da ANS). */
export function BenchmarkCard() {
  const f = useFilters();
  const [segmento, setSegmento] = useState("medicina_de_grupo");
  const b = useApi<Benchmark>(
    f.competencia ? `/analytics/sinistralidade/benchmark?competencia=${f.competencia}&segmento=${segmento}` : null,
  );
  const d = b.data;
  return (
    <Card
      title="Comparação com o Mercado"
      action={
        <Select className="!w-auto" value={segmento} onChange={(e) => setSegmento(e.target.value)} aria-label="Segmento de comparação">
          {(d?.segmentos ?? [{ codigo: segmento, rotulo: segmento }]).map((s) => (
            <option key={s.codigo} value={s.codigo}>
              {s.rotulo}
            </option>
          ))}
        </Select>
      }
    >
      <DataState isLoading={b.isLoading && !d} error={b.error} empty={d != null && !d.disponivel}>
        {d && (
          <div className="space-y-4">
            {d.ilustrativo && (
              <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
                <StatusBadge tone="warning">Ilustrativo</StatusBadge> {d.aviso}
              </div>
            )}
            <div className="grid gap-4 md:grid-cols-2">
              {d.itens.map((i) => (
                <div key={i.indicador} className="rounded-lg border border-slate-100 p-3">
                  <div className="text-xs font-medium uppercase tracking-wide text-slate-400">{i.rotulo}</div>
                  <div className="mt-1 flex flex-wrap items-baseline gap-2">
                    <span className="text-2xl font-semibold text-slate-900">{fmtValor(i.indicador, i.operadora)}</span>
                    {i.posicao && <StatusBadge tone={TOM[i.posicao]}>{i.posicao_rotulo}</StatusBadge>}
                  </div>
                  <Regua item={i} />
                  {i.referencia && (
                    <div className="mt-1 flex justify-between text-xs text-slate-500">
                      <span>p25 {fmtValor(i.indicador, i.referencia.p25)}</span>
                      <span>mediana {fmtValor(i.indicador, i.referencia.mediana)}</span>
                      <span>p75 {fmtValor(i.indicador, i.referencia.p75)}</span>
                    </div>
                  )}
                </div>
              ))}
            </div>
            <p className="text-xs text-slate-400">
              Operadora: últimos {d.janela.meses} meses
              {d.janela.inicio && d.janela.fim && ` (${fmtCompetencia(d.janela.inicio)} a ${fmtCompetencia(d.janela.fim)})`}.
              Mercado: {d.segmento_rotulo}
              {d.fonte === "ans_dados_abertos" ? ", dados abertos da ANS" : ""}. {d.limitacoes.join(" ")}
            </p>
          </div>
        )}
      </DataState>
    </Card>
  );
}
