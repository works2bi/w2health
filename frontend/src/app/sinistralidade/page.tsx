"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";

import { BenchmarkCard } from "@/components/BenchmarkCard";
import { CausasPanel } from "@/components/CausasPanel";
import { CompletudeBanner } from "@/components/CompletudeBanner";
import { CompositionCard } from "@/components/CompositionCard";
import { InvestigacaoPanel } from "@/components/InvestigacaoPanel";
import { ReceitaCard } from "@/components/ReceitaCard";
import { ConcentracaoBeneficiariosCard } from "@/components/ConcentracaoBeneficiariosCard";
import { MiniSeries, WaterfallChart } from "@/components/charts";
import { KpiInfo } from "@/components/KpiInfo";
import { Card, DataState, EfeitoBadge, PlanRestrictionNote, Stat } from "@/components/ui";
import type { Bridge, Fator } from "@/lib/api";
import { filtersQuery, useFilters } from "@/lib/filters";
import { fmtBRL, fmtBRLCompact, fmtCompetencia, fmtNum, fmtPct, fmtPP, fmtSignedPct, LABEL_DIMENSAO } from "@/lib/format";
import { useApi } from "@/lib/useApi";

const DIMS = ["grupo_despesa", "tipo_atendimento", "especialidade", "procedimento", "prestador", "regiao", "faixa_etaria", "sexo", "plano", "contrato"];

interface Indicador {
  sinistralidade_atual: number;
  sinistralidade_bruta: number;
  sinistralidade_comparacao: number;
  variacao_pp: number;
  despesa: number;
  receita: number;
  competencia_comparacao: string | null;
  decomposicao_receita_despesa: { efeito_despesa_pp: number; efeito_receita_pp: number } | null;
}
interface Explain {
  variacao_pp: number;
  efeito_despesa_pp: number;
  efeito_receita_pp: number;
  variacao_despesa: number;
  principais_fatores: Fator[];
  fatores_reducao: Fator[];
}
interface Drill {
  fator: Fator & { despesa_anterior: number; despesa_atual: number };
  serie: { competencia: string; despesa: number; eventos: number; custo_medio: number }[];
  onde_investigar: {
    prestadores_maior_despesa: { id: number; rotulo: string; despesa: number; eventos: number }[];
    beneficiarios_maior_despesa: { id: number; rotulo: string; despesa: number; eventos: number }[];
    prestadores_maior_contribuicao_variacao: { id: number; rotulo: string; delta: number }[];
  };
  restricoes_plano?: string[];
}

export default function SinistralidadePage() {
  const f = useFilters();
  const router = useRouter();
  const params = useSearchParams();
  const dimensao = params.get("dimensao") ?? "especialidade";
  const chave = params.get("chave");

  const setParam = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params.toString());
    for (const [k, v] of Object.entries(patch)) v == null ? next.delete(k) : next.set(k, v);
    router.replace(`/sinistralidade?${next.toString()}`, { scroll: false });
  };

  const ready = f.competencia != null;
  const emContrato = f.contratoId != null;
  const catContratos = useApi<{ itens: { id: number; nome: string }[] }>("/catalogos/contratos");
  const ind = useApi<Indicador>(ready && !emContrato ? `/analytics/sinistralidade${filtersQuery(f)}` : null);
  const exp = useApi<Explain & { aviso?: string }>(
    ready ? `/analytics/sinistralidade/explain${filtersQuery(f, { dimensao }, { withContrato: true })}` : null,
  );
  const drill = useApi<Drill>(
    ready && chave
      ? `/analytics/sinistralidade/explain/${dimensao}/${chave}${filtersQuery(f, {}, { withContrato: true })}`
      : null,
  );

  return (
    <div className="space-y-5">
      <CompletudeBanner />
      {/* escopo de contrato (v1.2) */}
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-slate-500">Escopo:</span>
        <select
          className="rounded-md border border-slate-300 px-2 py-1 text-xs"
          value={f.contratoId ?? ""}
          onChange={(e) => f.setContrato(e.target.value || null)}
        >
          <option value="">Todos os contratos</option>
          {(catContratos.data?.itens ?? []).map((c) => (
            <option key={c.id} value={c.id}>
              {c.nome}
            </option>
          ))}
        </select>
        {emContrato && (
          <span className="rounded-full bg-amber-50 px-2 py-0.5 text-xs text-amber-800">
            Sem receita por contrato — análise limitada à despesa
          </span>
        )}
      </div>

      {/* breadcrumb de investigação */}
      {chave && (
        <div className="flex items-center gap-1.5 text-xs text-slate-500">
          <button className="hover:underline" onClick={() => setParam({ chave: null })}>
            {LABEL_DIMENSAO[dimensao]}
          </button>
          <span>/</span>
          <span className="font-medium text-slate-700">{drill.data?.fator.categoria ?? chave}</span>
        </div>
      )}

      {!emContrato && (
        <DataState isLoading={ind.isLoading && !ind.data} error={ind.error}>
          {ind.data && (
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <Stat label={`Sinistralidade líquida — ${f.competencia ? fmtCompetencia(`${f.competencia}-01`) : ""}`} value={fmtPct(ind.data.sinistralidade_atual)} info={<KpiInfo kpi="sinistralidade_liquida" />} />
              <Stat label="Sinistralidade bruta" value={fmtPct(ind.data.sinistralidade_bruta)} hint="despesa bruta / receita" info={<KpiInfo kpi="sinistralidade_bruta" />} />
              <Stat label="Comparação (líquida)" value={fmtPct(ind.data.sinistralidade_comparacao)} hint={f.comparacao === "ano_anterior" ? "ano anterior" : "mês anterior"} />
              <Stat label="Variação (líquida)" value={fmtPP(ind.data.variacao_pp)} info={<KpiInfo kpi="variacao_pp" />} />
            </div>
          )}
        </DataState>
      )}

      <ConcentracaoBeneficiariosCard />

      {!emContrato && <CompositionCard />}

      {!emContrato && <ReceitaCard />}

      {!emContrato && <BenchmarkCard />}

      {ind.data?.decomposicao_receita_despesa && (
        <Card title="A variação (líquida) veio de despesa ou de receita?">
          <div className="flex flex-wrap items-center gap-6">
            <div>
              <div className="text-xs text-slate-400">Efeito despesa assistencial (líquida)</div>
              <div className="text-xl font-semibold text-rose-600">
                {fmtPP(ind.data.decomposicao_receita_despesa.efeito_despesa_pp)}
              </div>
            </div>
            <div>
              <div className="text-xs text-slate-400">Efeito comportamento da receita</div>
              <div className="text-xl font-semibold text-slate-700">
                {fmtPP(ind.data.decomposicao_receita_despesa.efeito_receita_pp)}
              </div>
            </div>
            <p className="max-w-md text-xs text-slate-500">
              Identidade exata: ΔS = ΔD/R₀ + (D₁/R₁ − D₁/R₀). A soma dos dois efeitos é a
              variação total em pontos percentuais.
            </p>
          </div>
        </Card>
      )}

      {/* Evolução V1 — investigação do escopo inteiro (operadora ou contrato filtrado) */}
      {!chave && <InvestigacaoPanel />}

      <Card
        title="Contribuição por dimensão"
        action={
          <select
            className="rounded-md border border-slate-300 px-2 py-1 text-xs"
            value={dimensao}
            onChange={(e) => setParam({ dimensao: e.target.value, chave: null })}
          >
            {DIMS.map((d) => (
              <option key={d} value={d}>
                {LABEL_DIMENSAO[d]}
              </option>
            ))}
          </select>
        }
      >
        <DataState isLoading={exp.isLoading && !exp.data} error={exp.error} empty={!exp.data?.principais_fatores?.length}>
          {exp.data && (
            <>
              <p className="mb-2 text-xs text-slate-500">
                Variação da despesa no período: <b>{fmtBRLCompact(exp.data.variacao_despesa)}</b>. Clique
                em um fator para investigar.
              </p>
              <WaterfallChart
                fatores={[...exp.data.principais_fatores, ...exp.data.fatores_reducao]}
                onSelect={(c) => setParam({ chave: c })}
              />
              <table className="mt-3 w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-100 text-left text-xs uppercase text-slate-400">
                    <th className="py-2">Categoria</th>
                    <th className="py-2 text-right">Δ despesa</th>
                    <th className="py-2 text-right">% variação</th>
                    <th className="py-2 text-right">Impacto</th>
                    <th className="py-2 text-right">Efeito</th>
                    <th className="py-2" />
                  </tr>
                </thead>
                <tbody>
                  {[...exp.data.principais_fatores, ...exp.data.fatores_reducao].map((r) => (
                    <tr
                      key={r.chave}
                      className={`border-b border-slate-50 ${chave === r.chave ? "bg-brand-50" : ""}`}
                    >
                      <td className="py-2">{r.categoria}</td>
                      <td className="py-2 text-right tabular-nums">{fmtBRLCompact(r.impacto_financeiro)}</td>
                      <td className="py-2 text-right tabular-nums">{r.participacao_variacao.toFixed(0)}%</td>
                      <td className="py-2 text-right tabular-nums">{fmtPP(r.impacto_pp)}</td>
                      <td className="py-2 text-right"><EfeitoBadge efeito={r.efeito_principal} /></td>
                      <td className="py-2 text-right">
                        <button
                          className="rounded-md border border-brand-200 bg-brand-50 px-2 py-0.5 text-xs text-brand-700 hover:bg-brand-100"
                          onClick={() => setParam({ chave: r.chave })}
                        >
                          investigar
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </DataState>
      </Card>

      {chave && (
        <DataState isLoading={drill.isLoading && !drill.data} error={drill.error}>
          {drill.data && <DrillPanel drill={drill.data} dimensao={dimensao} f={f} />}
        </DataState>
      )}
    </div>
  );
}

function BridgeView({ b }: { b: Bridge }) {
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <div className="rounded-lg border border-brand-100 bg-brand-50 p-3">
        <div className="text-xs font-medium text-brand-700">Efeito frequência</div>
        <div className="text-xl font-semibold text-brand-900">{fmtBRLCompact(b.efeito_frequencia)}</div>
        <div className="mt-1 text-xs text-slate-500">
          {fmtNum(b.qtd_anterior)} → {fmtNum(b.qtd_atual)} eventos (
          {fmtSignedPct(b.variacao_frequencia_pct ?? null)})
        </div>
      </div>
      <div className="rounded-lg border border-gold-400 bg-gold-50 p-3">
        <div className="text-xs font-medium text-gold-700">Efeito custo médio</div>
        <div className="text-xl font-semibold text-brand-900">{fmtBRLCompact(b.efeito_custo_medio)}</div>
        <div className="mt-1 text-xs text-slate-500">
          {fmtBRL(b.custo_medio_anterior, true)} → {fmtBRL(b.custo_medio_atual, true)} (
          {fmtSignedPct(b.variacao_custo_medio_pct ?? null)})
        </div>
      </div>
    </div>
  );
}

function DrillPanel({
  drill,
  dimensao,
  f,
}: {
  drill: Drill;
  dimensao: string;
  f: ReturnType<typeof useFilters>;
}) {
  const b = drill.fator.bridge;
  return (
    <div className="space-y-4">
      <Card
        title={
          <span>
            {drill.fator.categoria} — <EfeitoBadge efeito={drill.fator.efeito_principal} />
          </span>
        }
      >
        <p className="mb-3 text-sm text-slate-600">
          Impacto de <b>{fmtBRL(drill.fator.impacto_financeiro)}</b> ({fmtPP(drill.fator.impacto_pp)} de
          sinistralidade). O movimento foi{" "}
          <b>
            {drill.fator.efeito_principal === "frequencia"
              ? "predominantemente por frequência"
              : drill.fator.efeito_principal === "custo_medio"
                ? "predominantemente por custo médio"
                : "misto (frequência + custo médio)"}
          </b>
          .
        </p>
        <BridgeView b={b} />
        <div className="mt-4">
          <div className="text-xs font-medium text-slate-500">Despesa mensal do fator</div>
          <MiniSeries serie={drill.serie} dataKey="despesa" />
        </div>
      </Card>

      <InvestigacaoPanel
        dimensao={dimensao}
        chave={drill.fator.chave}
        titulo={`Por que a despesa de ${drill.fator.categoria} mudou?`}
      />

      <CausasPanel dimensao={dimensao} chave={drill.fator.chave} categoria={drill.fator.categoria} />

      <PlanRestrictionNote restricoes={drill.restricoes_plano} />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Onde investigar primeiro — prestadores">
          <table className="w-full text-sm">
            <tbody>
              {drill.onde_investigar.prestadores_maior_contribuicao_variacao.slice(0, 6).map((p) => (
                <tr key={p.id} className="border-b border-slate-50">
                  <td className="py-1.5">
                    <Link href={`/prestadores/${p.id}${filtersQuery(f)}`} className="text-brand-600 hover:underline">
                      {p.rotulo}
                    </Link>
                  </td>
                  <td className="py-1.5 text-right tabular-nums text-slate-600">{fmtBRLCompact(p.delta)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <Card title="Onde investigar primeiro — beneficiários">
          <table className="w-full text-sm">
            <tbody>
              {drill.onde_investigar.beneficiarios_maior_despesa.slice(0, 6).map((p) => (
                <tr key={p.id} className="border-b border-slate-50">
                  <td className="py-1.5">
                    <Link href={`/beneficiarios/${p.id}`} className="text-brand-600 hover:underline">
                      {p.rotulo}
                    </Link>
                  </td>
                  <td className="py-1.5 text-right tabular-nums text-slate-600">{fmtBRLCompact(p.despesa)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>

      {dimensao !== "prestador" && (
        <p className="text-xs text-slate-400">
          Dica: clique em um prestador para chegar aos eventos e beneficiários relacionados.
        </p>
      )}
    </div>
  );
}
