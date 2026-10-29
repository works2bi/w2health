"use client";

import Link from "next/link";

import { CompletudeBanner } from "@/components/CompletudeBanner";
import { CompositionCard } from "@/components/CompositionCard";
import { EvolutionChart } from "@/components/charts";
import { InsightChip } from "@/components/InsightCard";
import { KpiInfo } from "@/components/KpiInfo";
import { Card, DataState, EfeitoBadge, PlanRestrictionNote, Stat } from "@/components/ui";
import type { Fator, Insight } from "@/lib/api";
import { filtersQuery, useFilters } from "@/lib/filters";
import { fmtBRL, fmtBRLCompact, fmtNum, fmtPct, fmtPP } from "@/lib/format";
import { useApi } from "@/lib/useApi";

interface Overview {
  competencia: string;
  kpis: {
    sinistralidade: number;
    variacao_pp: number;
    receita: number;
    despesa: number;
    beneficiarios: number;
    custo_assistencial_por_beneficiario: number;
    receita_media_por_beneficiario: number;
    variacao_pp_ano_anterior: number | null;
    acumulado_12m: number | null;
  };
  decomposicao_receita_despesa: { efeito_despesa_pp: number; efeito_receita_pp: number } | null;
  serie: { competencia: string; sinistralidade: number; acumulado_12m: number | null }[];
  principais_fatores_atencao: Insight[];
  restricoes_plano?: string[];
}

interface Explain {
  variacao_pp: number;
  efeito_despesa_pp: number;
  efeito_receita_pp: number;
  principais_fatores: Fator[];
  fatores_reducao: Fator[];
}

export default function VisaoExecutivaPage() {
  const f = useFilters();
  const ready = f.competencia != null;
  const q = filtersQuery(f);
  const ov = useApi<Overview>(ready ? `/executive/overview${q}` : null);
  const esp = useApi<Explain>(
    ready ? `/analytics/sinistralidade/explain${filtersQuery(f, { dimensao: "especialidade" })}` : null,
  );
  const grp = useApi<{ principais_fatores: Fator[] }>(
    ready ? `/analytics/sinistralidade/explain${filtersQuery(f, { dimensao: "grupo_despesa" })}` : null,
  );

  const k = ov.data?.kpis;
  const comparacaoTxt = f.comparacao === "ano_anterior" ? "o mesmo mês do ano anterior" : "o mês anterior";

  return (
    <div className="space-y-6">
      <CompletudeBanner />
      <DataState isLoading={ov.isLoading && !ov.data} error={ov.error}>
        {k && (
          <>
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <Stat
                label="Sinistralidade (líquida)"
                value={fmtPct(k.sinistralidade)}
                delta={k.variacao_pp}
                deltaKind="pp"
                invertColors
                hint={f.comparacao === "ano_anterior" ? "vs ano anterior" : "vs mês anterior"}
                href={`/sinistralidade${q}`}
                info={<KpiInfo kpi="sinistralidade_liquida" />}
              />
              <Stat label="Receita de contraprestações" value={fmtBRLCompact(k.receita)} info={<KpiInfo kpi="receita" />} />
              <Stat label="Despesa assistencial" value={fmtBRLCompact(k.despesa)} info={<KpiInfo kpi="despesa_liquida" />} />
              <Stat label="Beneficiários" value={fmtNum(k.beneficiarios)} info={<KpiInfo kpi="beneficiarios" />} />
              <Stat
                label="Custo assistencial / beneficiário"
                value={fmtBRL(k.custo_assistencial_por_beneficiario, true)}
                info={<KpiInfo kpi="custo_pmpm" />}
              />
              <Stat
                label="Receita média / beneficiário"
                value={fmtBRL(k.receita_media_por_beneficiario, true)}
              />
              <Stat
                label="Sinistralidade — 12 meses"
                value={k.acumulado_12m != null ? fmtPct(k.acumulado_12m) : "—"}
                info={<KpiInfo kpi="acumulado_12m" />}
              />
              <Stat
                label="Variação vs ano anterior"
                value={k.variacao_pp_ano_anterior != null ? `${k.variacao_pp_ano_anterior} p.p.` : "—"}
              />
            </div>

            <PorQueCard
              variacaoPp={k.variacao_pp}
              comparacaoTxt={comparacaoTxt}
              dec={ov.data!.decomposicao_receita_despesa}
              esp={esp.data}
              loading={esp.isLoading && !esp.data}
              q={q}
            />

            <CompositionCard />

            <Card title="Evolução mensal da sinistralidade">
              <EvolutionChart serie={ov.data!.serie} competenciaAtual={f.competencia} />
              <p className="mt-2 text-xs text-slate-400">
                Linha dourada: sinistralidade do mês. Linha steel tracejada: acumulado 12 meses.
              </p>
            </Card>

            <Card title="Principais fatores de atenção">
              <PlanRestrictionNote restricoes={ov.data!.restricoes_plano} />
              <div className="grid gap-2 sm:grid-cols-2">
                {ov.data!.principais_fatores_atencao.map((i) => (
                  <InsightChip key={i.id} insight={i} />
                ))}
              </div>
              <div className="mt-3 text-right">
                <Link href="/insights" className="text-xs font-medium text-brand-600 hover:underline">
                  Ver todos os insights →
                </Link>
              </div>
            </Card>
          </>
        )}
      </DataState>

      <Card title="Top grupos de despesa por contribuição à variação">
        <DataState isLoading={grp.isLoading && !grp.data} error={grp.error} empty={!grp.data?.principais_fatores?.length}>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-100 text-left text-xs uppercase text-slate-400">
                <th className="py-2">Grupo</th>
                <th className="py-2 text-right">Δ despesa</th>
                <th className="py-2 text-right">% da variação</th>
                <th className="py-2 text-right">Efeito</th>
              </tr>
            </thead>
            <tbody>
              {(grp.data?.principais_fatores ?? []).slice(0, 6).map((r) => (
                <tr key={r.chave} className="border-b border-slate-50">
                  <td className="py-2">
                    <Link
                      href={`/sinistralidade${filtersQuery(f, { dimensao: "grupo_despesa", chave: r.chave })}`}
                      className="text-brand-600 hover:underline"
                    >
                      {r.categoria}
                    </Link>
                  </td>
                  <td className="py-2 text-right tabular-nums">{fmtBRLCompact(r.impacto_financeiro)}</td>
                  <td className="py-2 text-right tabular-nums">{r.participacao_variacao.toFixed(0)}%</td>
                  <td className="py-2 text-right">
                    <EfeitoBadge efeito={r.efeito_principal} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </DataState>
      </Card>
    </div>
  );
}

function PorQueCard({
  variacaoPp,
  comparacaoTxt,
  dec,
  esp,
  loading,
  q,
}: {
  variacaoPp: number;
  comparacaoTxt: string;
  dec: { efeito_despesa_pp: number; efeito_receita_pp: number } | null;
  esp?: Explain;
  loading: boolean;
  q: string;
}) {
  const caiu = variacaoPp < 0;
  const verbo = Math.abs(variacaoPp) < 0.05 ? "ficou estável" : caiu ? "caiu" : "subiu";

  // frase despesa × receita
  let fraseNumDen: string | null = null;
  if (dec) {
    const d = dec.efeito_despesa_pp;
    const r = dec.efeito_receita_pp;
    const tot = Math.abs(d) + Math.abs(r);
    if (tot > 0.1) {
      const pesoDesp = Math.abs(d) / tot;
      if (pesoDesp >= 0.8) {
        fraseNumDen = `Praticamente toda a variação (${d.toFixed(1)} p.p.) veio da despesa assistencial.`;
      } else if (pesoDesp <= 0.2) {
        fraseNumDen = `A variação veio quase toda do comportamento da receita (${r.toFixed(1)} p.p.), não da despesa.`;
      } else {
        fraseNumDen = `${d.toFixed(1)} p.p. vieram da despesa assistencial e ${r.toFixed(1)} p.p. do comportamento da receita (contraprestações).`;
      }
    }
  }

  const fatores = esp
    ? [...esp.principais_fatores, ...esp.fatores_reducao]
        .filter((x) => Math.abs(x.impacto_financeiro) > 0)
        .sort((a, b) => Math.abs(b.impacto_pp) - Math.abs(a.impacto_pp))
        .slice(0, 4)
    : [];

  return (
    <Card
      title={
        <span>
          Por que a sinistralidade {verbo}{" "}
          {Math.abs(variacaoPp) >= 0.05 && (
            <b className={caiu ? "text-emerald-600" : "text-rose-600"}>
              {fmtPP(variacaoPp)}
            </b>
          )}{" "}
          vs {comparacaoTxt}?
        </span>
      }
      action={
        <Link href={`/sinistralidade${q}`} className="text-xs font-medium text-brand-600 hover:underline">
          Explicação completa →
        </Link>
      }
    >
      {fraseNumDen && <p className="text-sm text-slate-600">{fraseNumDen}</p>}

      <DataState isLoading={loading} error={undefined} empty={!loading && fatores.length === 0}>
        <p className="mb-1 mt-3 text-xs font-medium uppercase tracking-wide text-slate-400">
          Especialidades que mais moveram o resultado
        </p>
        <ul className="divide-y divide-slate-100">
          {fatores.map((r) => {
            const puxouPraBaixo = r.impacto_financeiro < 0;
            return (
              <li key={r.chave} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2 text-sm">
                <span
                  className={`inline-block h-2 w-2 shrink-0 rounded-full ${
                    puxouPraBaixo ? "bg-emerald-500" : "bg-rose-500"
                  }`}
                  title={puxouPraBaixo ? "puxou a sinistralidade para baixo" : "empurrou a sinistralidade para cima"}
                />
                <span className="min-w-0 flex-1 font-medium text-slate-800">{r.categoria}</span>
                <span className="tabular-nums text-slate-600">{fmtBRLCompact(r.impacto_financeiro)}</span>
                <span className="tabular-nums text-slate-400">{Math.abs(r.participacao_variacao).toFixed(0)}% da variação</span>
                <EfeitoBadge efeito={r.efeito_principal} />
                <Link
                  href={`/sinistralidade${q}&dimensao=especialidade&chave=${r.chave}`}
                  className="rounded-md border border-brand-200 bg-brand-50 px-2 py-0.5 text-xs font-medium text-brand-700 hover:bg-brand-100"
                >
                  investigar
                </Link>
              </li>
            );
          })}
        </ul>
        <p className="mt-2 text-xs text-slate-400">
          🟢 puxou para baixo · 🔴 empurrou para cima. Clique em &ldquo;investigar&rdquo; para ver o
          bridge frequência × custo médio e os prestadores/beneficiários por trás.
        </p>
      </DataState>
    </Card>
  );
}
