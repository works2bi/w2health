"use client";

import { useState } from "react";

import { Card, DataState, Field, Input, StatusBadge, Toggle } from "@/components/ui";
import { useFilters } from "@/lib/filters";
import { fmtBRLCompact, fmtCompetencia, fmtPct } from "@/lib/format";
import { useApi } from "@/lib/useApi";

interface Simulacao {
  disponivel: boolean;
  motivo?: string;
  exige_receita_informada?: boolean;
  fonte_receita?: "reconhecida_atribuida" | "informada";
  historico?: {
    meses: number;
    receita_reconhecida: number;
    despesa_liquida_observada: number;
    despesa_liquida_considerada: number;
    ajuste_completude: number;
    sinistralidade_pct: number;
    rotulo: string;
  };
  projecao?: {
    despesa_liquida_projetada: number;
    receita_necessaria: number;
    reajuste_tecnico_pct: number;
    sinistralidade_sem_reajuste: number;
    rotulo: string;
  };
  cenarios?: { reajuste_pct: number; sinistralidade_projetada: number; atinge_meta: boolean }[];
  serie?: { competencia: string; preliminar: boolean }[];
  formula?: string;
  limitacoes?: string[];
}

/** Item 2 — reajuste técnico necessário para a sinistralidade do contrato voltar à meta. */
export function SimuladorReajuste({ contratoId }: { contratoId: string }) {
  const f = useFilters();
  const [meta, setMeta] = useState("75");
  const [vcmh, setVcmh] = useState("10");
  const [receita, setReceita] = useState("");
  const [completar, setCompletar] = useState(true);
  const p = new URLSearchParams({ meta, vcmh, ajustar_completude: String(completar) });
  if (f.competencia) p.set("competencia", f.competencia);
  if (receita) p.set("receita_mensal", receita);
  const ok = Number(meta) > 0 && vcmh !== "";
  const s = useApi<Simulacao>(ok ? `/analytics/contratos/${contratoId}/simulacao-reajuste?${p}` : null);
  const d = s.data;

  return (
    <Card title="Simulador de Reajuste">
      <div className="grid gap-3 sm:grid-cols-4">
        <Field label="Meta de sinistralidade (%)">
          {(id) => <Input id={id} type="number" min={1} max={200} value={meta} onChange={(e) => setMeta(e.target.value)} />}
        </Field>
        <Field label="VCMH projetada (%)">
          {(id) => <Input id={id} type="number" min={-50} max={100} value={vcmh} onChange={(e) => setVcmh(e.target.value)} />}
        </Field>
        <Field label="Receita mensal informada (R$)">
          {(id) => <Input
            type="number"
            min={0}
            placeholder={d?.fonte_receita === "reconhecida_atribuida" ? "usa a receita reconhecida" : "obrigatória sem receita do contrato"}
            value={receita}
            onChange={(e) => setReceita(e.target.value)}
          />}
        </Field>
        <Field label="Completar meses preliminares">
          {() => <Toggle checked={completar} onChange={setCompletar} label="Somar contas a chegar" />}
        </Field>
      </div>

      <div className="mt-4">
        <DataState isLoading={s.isLoading && !d} error={s.error}>
          {d && !d.disponivel && (
            <p className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-600">{d.motivo}</p>
          )}
          {d?.disponivel && d.historico && d.projecao && (
            <div className="space-y-4">
              <div className="grid gap-3 sm:grid-cols-3">
                <div className="rounded-lg border border-slate-100 p-3">
                  <div className="text-xs uppercase text-slate-400">Sinistralidade últimos {d.historico.meses} meses</div>
                  <div className="text-2xl font-semibold">{fmtPct(d.historico.sinistralidade_pct)}</div>
                  <StatusBadge tone={d.historico.rotulo.startsWith("FATO") ? "success" : "warning"}>{d.historico.rotulo}</StatusBadge>
                </div>
                <div className="rounded-lg border border-slate-100 p-3">
                  <div className="text-xs uppercase text-slate-400">Sem reajuste, com a VCMH</div>
                  <div className="text-2xl font-semibold">{fmtPct(d.projecao.sinistralidade_sem_reajuste)}</div>
                  <StatusBadge tone="warning">{d.projecao.rotulo}</StatusBadge>
                </div>
                <div className="rounded-lg border border-gold-400 bg-gold-50 p-3">
                  <div className="text-xs uppercase text-slate-500">Reajuste técnico para {meta}%</div>
                  <div className="text-2xl font-semibold text-slate-900">{fmtPct(d.projecao.reajuste_tecnico_pct)}</div>
                  <div className="text-xs text-slate-500">
                    receita necessária {fmtBRLCompact(d.projecao.receita_necessaria)} em {d.historico.meses} meses
                  </div>
                </div>
              </div>
              {d.historico.ajuste_completude > 0 && (
                <p className="text-xs text-amber-800">
                  Inclui {fmtBRLCompact(d.historico.ajuste_completude)} de contas a chegar (ESTIMATIVA) nos meses{" "}
                  {(d.serie ?? []).filter((x) => x.preliminar).map((x) => fmtCompetencia(x.competencia)).join(", ")}.
                </p>
              )}
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-100 text-left text-xs uppercase text-slate-400">
                    <th className="py-2">Reajuste</th>
                    <th className="py-2 text-right">Sinistralidade projetada</th>
                    <th className="py-2 text-right">Meta</th>
                  </tr>
                </thead>
                <tbody>
                  {(d.cenarios ?? []).map((c) => (
                    <tr key={c.reajuste_pct} className="border-b border-slate-50">
                      <td className="py-1.5 tabular-nums">{fmtPct(c.reajuste_pct)}</td>
                      <td className="py-1.5 text-right tabular-nums">{fmtPct(c.sinistralidade_projetada)}</td>
                      <td className="py-1.5 text-right">
                        {c.atinge_meta ? <StatusBadge tone="success">Atinge</StatusBadge> : <StatusBadge tone="danger">Acima</StatusBadge>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="text-xs text-slate-400">
                {d.formula}. {(d.limitacoes ?? []).join(" ")}
              </p>
            </div>
          )}
        </DataState>
      </div>
    </Card>
  );
}
