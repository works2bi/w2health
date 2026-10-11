"use client";

import Link from "next/link";

import { MiniSeries } from "@/components/charts";
import { Card, DataState, EfeitoBadge, Stat } from "@/components/ui";
import { filtersQuery, useFilters } from "@/lib/filters";
import { fmtBRL, fmtBRLCompact, fmtNum, fmtPct } from "@/lib/format";
import { useApi } from "@/lib/useApi";
import { useRouteId } from "@/lib/useRouteId";

interface Detalhe {
  contrato: { id: number; nome: string; tipo: string; plano: string; vidas_alvo: number };
  receita_disponivel: false;
  aviso: string;
  kpis: {
    vidas: number;
    despesa_bruta: number;
    glosas: number;
    coparticipacao: number;
    despesa_liquida: number;
    custo_pmpm: number;
    eventos: number;
    gini: number;
    top5_share: number;
    n_beneficiarios_alto_custo: number;
    variacao_despesa_liquida_pct: number | null;
  };
  composicao: {
    atual: { despesa_bruta: number; glosas: number; coparticipacao: number; despesa_liquida: number } | null;
  };
  evolucao: {
    competencia: string;
    vidas: number;
    despesa_liquida: number;
    custo_pmpm: number;
    eventos: number;
    gini: number;
  }[];
  concentracao: {
    gini: number;
    pareto_frac: number;
    top_beneficiarios: {
      id: number;
      codigo: string;
      despesa_liquida: number;
      eventos: number;
      participacao_pct: number;
    }[];
  };
  drivers: {
    principais_fatores: { chave: string; categoria: string; impacto_financeiro: number; efeito_principal: string }[];
    fatores_reducao: { chave: string; categoria: string; impacto_financeiro: number }[];
  } | null;
  alertas: { regra_nome: string; rotulo: string; valor_observado: number; severidade: string; deep_link: { rota: string } }[];
}

export default function ContratoDetalhePage() {
  const id = useRouteId();
  const f = useFilters();
  const { data, error, isLoading } = useApi<Detalhe>(
    `/analytics/contratos/${id}${filtersQuery(f)}`,
  );

  return (
    <div className="space-y-4">
      <nav className="text-sm text-slate-500">
        <Link href={`/contratos${filtersQuery(f)}`} className="hover:underline">
          Contratos
        </Link>
        <span className="mx-1">/</span>
        <span className="text-slate-700">{data?.contrato.nome ?? id}</span>
      </nav>

      <DataState isLoading={isLoading} error={error} empty={!data}>
        {data && (
          <>
            <header>
              <h1 className="text-lg font-semibold text-slate-900">{data.contrato.nome}</h1>
              <p className="text-sm text-slate-500">
                {data.contrato.plano} · {data.contrato.tipo}
              </p>
            </header>

            <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-800">
              ⚠️ {data.aviso}
            </div>

            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <Stat label="Vidas" value={fmtNum(data.kpis.vidas)} />
              <Stat
                label="Despesa líquida"
                value={`${fmtBRLCompact(data.kpis.despesa_liquida)}`}
                delta={data.kpis.variacao_despesa_liquida_pct}
                deltaKind="pct"
              />
              <Stat label="Custo PMPM" value={`${fmtBRLCompact(data.kpis.custo_pmpm)}`} />
              <Stat
                label="Concentração (top-5)"
                value={fmtPct(data.kpis.top5_share * 100)}
                hint={`Gini ${data.kpis.gini.toFixed(2)} · ${data.kpis.n_beneficiarios_alto_custo} de alto custo`}
              />
            </div>

            <div className="grid gap-4 lg:grid-cols-2">
              <Card title="Composição da despesa (mês)">
                {data.composicao.atual && (
                  <ul className="space-y-1 text-sm">
                    <li className="flex justify-between">
                      <span className="text-slate-500">Despesa bruta</span>
                      <span className="tabular-nums">R$ {fmtBRL(data.composicao.atual.despesa_bruta)}</span>
                    </li>
                    <li className="flex justify-between text-emerald-700">
                      <span>(−) Glosas</span>
                      <span className="tabular-nums">R$ {fmtBRL(data.composicao.atual.glosas)}</span>
                    </li>
                    <li className="flex justify-between text-emerald-700">
                      <span>(−) Coparticipação</span>
                      <span className="tabular-nums">R$ {fmtBRL(data.composicao.atual.coparticipacao)}</span>
                    </li>
                    <li className="flex justify-between border-t border-slate-100 pt-1 font-semibold">
                      <span>= Despesa líquida</span>
                      <span className="tabular-nums">R$ {fmtBRL(data.composicao.atual.despesa_liquida)}</span>
                    </li>
                  </ul>
                )}
              </Card>

              <Card title="Evolução da despesa líquida">
                <MiniSeries serie={data.evolucao} dataKey="despesa_liquida" />
              </Card>
            </div>

            <Card
              title="Concentração — beneficiários de maior impacto"
              action={
                <span className="text-xs text-slate-400">
                  Gini {data.concentracao.gini.toFixed(2)} · Pareto {fmtPct(data.concentracao.pareto_frac * 100)}
                </span>
              }
            >
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-100 text-left text-xs uppercase text-slate-400">
                      <th className="py-2 pr-3">Beneficiário</th>
                      <th className="px-3 text-right">Despesa líquida</th>
                      <th className="px-3 text-right">Eventos</th>
                      <th className="px-3 text-right">% do contrato</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.concentracao.top_beneficiarios.map((b) => (
                      <tr key={b.id} className="border-b border-slate-50 hover:bg-slate-50">
                        <td className="py-2 pr-3">
                          <Link href={`/beneficiarios/${b.id}`} className="text-brand-700 hover:underline">
                            {b.codigo}
                          </Link>
                        </td>
                        <td className="px-3 text-right">{fmtBRLCompact(b.despesa_liquida)}</td>
                        <td className="px-3 text-right">{b.eventos}</td>
                        <td className="px-3 text-right font-medium">{fmtPct(b.participacao_pct)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>

            {data.drivers && (
              <Card title="Drivers da despesa dentro do contrato (por especialidade)">
                <ul className="space-y-1 text-sm">
                  {data.drivers.principais_fatores.map((d) => (
                    <li key={d.chave} className="flex items-center justify-between">
                      <span className="flex items-center gap-2">
                        {d.categoria} <EfeitoBadge efeito={d.efeito_principal} />
                      </span>
                      <span className="tabular-nums text-rose-600">
                        {fmtBRLCompact(d.impacto_financeiro)}
                      </span>
                    </li>
                  ))}
                  {data.drivers.fatores_reducao.map((d) => (
                    <li key={d.chave} className="flex items-center justify-between text-emerald-700">
                      <span>{d.categoria}</span>
                      <span className="tabular-nums">{fmtBRLCompact(d.impacto_financeiro)}</span>
                    </li>
                  ))}
                </ul>
              </Card>
            )}

            {data.alertas.length > 0 && (
              <Card title={`Alertas do contrato (${data.alertas.length})`}>
                <ul className="space-y-1 text-sm">
                  {data.alertas.map((a, i) => (
                    <li key={i} className="flex justify-between">
                      <span>
                        <span className="font-medium">{a.regra_nome}</span>{" "}
                        <span className="text-slate-500">— {a.rotulo}</span>
                      </span>
                      <span className="tabular-nums text-slate-600">{a.valor_observado}</span>
                    </li>
                  ))}
                </ul>
              </Card>
            )}
          </>
        )}
      </DataState>
    </div>
  );
}
