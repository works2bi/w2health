"use client";


import { MiniSeries } from "@/components/charts";
import { Card, DataState, EfeitoBadge, Stat } from "@/components/ui";
import type { Bridge } from "@/lib/api";
import { filtersQuery, useFilters } from "@/lib/filters";
import { fmtBRL, fmtBRLCompact, fmtNum, fmtSignedPct } from "@/lib/format";
import { useApi } from "@/lib/useApi";
import { useRouteId } from "@/lib/useRouteId";

interface Detalhe {
  prestador: { id: number; nome: string; tipo: string; regiao: string; especialidade_principal: string };
  competencia: string;
  kpis: { despesa: number; eventos: number; beneficiarios: number; custo_medio: number; participacao: number };
  bridge: Bridge;
  serie: { competencia: string; despesa: number; eventos: number; custo_medio: number }[];
  principais_procedimentos: { id: number; descricao: string; grupo: string; eventos: number; despesa: number; custo_medio: number }[];
  concentracao: { top_k_share: Record<string, number>; gini: number; pareto_frac: number };
  comparacao_pares: { n_pares: number; zscores: Record<string, number>; fora_padrao: string[] };
}

export default function PrestadorDetalhePage() {
  const f = useFilters();
  const id = useRouteId();
  const ready = f.competencia != null;
  const d = useApi<Detalhe>(ready ? `/analytics/prestadores/${id}${filtersQuery(f)}` : null);

  return (
    <DataState isLoading={d.isLoading && !d.data} error={d.error}>
      {d.data && (
        <div className="space-y-5">
          <div>
            <h2 className="text-lg font-semibold text-slate-900">
              {d.data.prestador.nome}
              {d.data.comparacao_pares.fora_padrao.length > 0 && <span className="ml-2">🚩</span>}
            </h2>
            <p className="text-sm text-slate-500">
              {d.data.prestador.tipo.replace("_", " ")} · {d.data.prestador.regiao} ·{" "}
              {d.data.prestador.especialidade_principal}
            </p>
          </div>

          <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
            <Stat label="Custo total" value={fmtBRLCompact(d.data.kpis.despesa)} />
            <Stat label="Eventos" value={fmtNum(d.data.kpis.eventos)} />
            <Stat label="Beneficiários" value={fmtNum(d.data.kpis.beneficiarios)} />
            <Stat label="Custo médio" value={fmtBRL(d.data.kpis.custo_medio, true)} />
            <Stat label="Participação no custo total" value={`${(d.data.kpis.participacao * 100).toFixed(1)}%`} />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card title={<span>Frequência × custo médio <EfeitoBadge efeito={d.data.bridge.efeito_principal} /></span>}>
              <div className="grid grid-cols-2 gap-3 text-sm">
                <div className="rounded-lg border border-brand-100 bg-brand-50 p-3">
                  <div className="text-xs text-brand-700">Efeito frequência</div>
                  <div className="text-lg font-semibold">{fmtBRLCompact(d.data.bridge.efeito_frequencia)}</div>
                  <div className="text-xs text-slate-500">{fmtSignedPct(d.data.bridge.variacao_frequencia_pct ?? null)} eventos</div>
                </div>
                <div className="rounded-lg border border-gold-400 bg-gold-50 p-3">
                  <div className="text-xs text-gold-700">Efeito custo médio</div>
                  <div className="text-lg font-semibold">{fmtBRLCompact(d.data.bridge.efeito_custo_medio)}</div>
                  <div className="text-xs text-slate-500">{fmtSignedPct(d.data.bridge.variacao_custo_medio_pct ?? null)} por evento</div>
                </div>
              </div>
            </Card>
            <Card title={`Comparação com pares (${d.data.comparacao_pares.n_pares})`}>
              <table className="w-full text-sm">
                <tbody>
                  {Object.entries(d.data.comparacao_pares.zscores).map(([k, v]) => (
                    <tr key={k} className="border-b border-slate-50">
                      <td className="py-1.5 text-slate-600">{k.replace(/_/g, " ")}</td>
                      <td className={`py-1.5 text-right font-medium tabular-nums ${Math.abs(v) >= 2 ? "text-amber-700" : "text-slate-500"}`}>
                        z = {v.toFixed(2)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {d.data.comparacao_pares.fora_padrao.length > 0 && (
                <p className="mt-2 rounded-md bg-amber-50 p-2 text-xs text-amber-800">
                  Fora do padrão em: {d.data.comparacao_pares.fora_padrao.join(", ")}.
                </p>
              )}
            </Card>
          </div>

          <Card title="Evolução mensal do custo">
            <MiniSeries serie={d.data.serie} dataKey="despesa" />
          </Card>

          <Card title="Principais procedimentos">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-100 text-left text-xs uppercase text-slate-400">
                  <th className="py-2">Procedimento</th>
                  <th className="py-2 text-right">Eventos</th>
                  <th className="py-2 text-right">Despesa</th>
                  <th className="py-2 text-right">Custo médio</th>
                </tr>
              </thead>
              <tbody>
                {d.data.principais_procedimentos.map((p) => (
                  <tr key={p.id} className="border-b border-slate-50">
                    <td className="py-2">{p.descricao}</td>
                    <td className="py-2 text-right tabular-nums">{fmtNum(p.eventos)}</td>
                    <td className="py-2 text-right tabular-nums">{fmtBRLCompact(p.despesa)}</td>
                    <td className="py-2 text-right tabular-nums">{fmtBRLCompact(p.custo_medio)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-2 text-xs text-slate-400">
              Concentração (Gini {d.data.concentracao.gini.toFixed(2)}) — top 3 procedimentos ={" "}
              {((d.data.concentracao.top_k_share["3"] ?? 0) * 100).toFixed(0)}% da despesa.
            </p>
          </Card>
        </div>
      )}
    </DataState>
  );
}
