"use client";

import { Regua } from "@/components/BenchmarkCard";
import { EvolutionChart } from "@/components/charts";
import { Button, Card, DataState, StatusBadge } from "@/components/ui";
import { useFilters } from "@/lib/filters";
import { fmtBRL, fmtBRLCompact, fmtCompetencia, fmtNum, fmtPct, fmtPP, fmtSignedPct } from "@/lib/format";
import { useApi } from "@/lib/useApi";

interface Relatorio {
  tenant: string;
  competencia: string;
  kpis: {
    sinistralidade: number;
    variacao_pp: number;
    receita: number;
    despesa: number;
    beneficiarios: number;
    custo_assistencial_por_beneficiario: number;
    variacao_pp_ano_anterior: number | null;
    acumulado_12m: number | null;
  };
  decomposicao_receita_despesa: { efeito_despesa_pp: number; efeito_receita_pp: number } | null;
  serie: { competencia: string; sinistralidade: number; acumulado_12m: number | null }[];
  completude: {
    status: string;
    completude_pct: number;
    ibnr_estimado: number;
    despesa_liquida_estimada_final: number;
    sinistralidade_projetada: number | null;
  } | null;
  principais_fatores: { titulo: string; descricao: string; severidade: string }[] | null;
  alertas: { total: number; por_severidade: Record<string, number> } | null;
  planos_acao: {
    total: number;
    por_status: Record<string, number>;
    atrasados: number;
    destaque: {
      titulo: string;
      alvo: string;
      responsavel: string;
      prazo: string | null;
      status: string;
      situacao_efeito: string;
      variacao_alvo_pct: number | null;
      efeito_relativo_pp: number | null;
    }[];
  } | null;
  mercado: {
    segmento_rotulo: string;
    ilustrativo: boolean;
    aviso: string | null;
    itens: {
      indicador: "sinistralidade_pct" | "despesa_pmpm";
      rotulo: string;
      operadora: number | null;
      referencia: { p25: number; mediana: number; p75: number; media_ponderada: number | null; n_operadoras: number; periodo: string } | null;
      posicao: string | null;
      posicao_rotulo: string | null;
      diferenca_mediana: number | null;
    }[];
  } | null;
  notas: string[];
}

const STATUS_PLANO: Record<string, string> = {
  aberto: "Aberto",
  em_andamento: "Em andamento",
  concluido: "Concluído",
  cancelado: "Cancelado",
};

/** Item 5 — relatório mensal da diretoria. "Gerar PDF" usa a impressão do navegador. */
export default function RelatorioPage() {
  const f = useFilters();
  const r = useApi<Relatorio>(f.competencia ? `/executive/relatorio-mensal?competencia=${f.competencia}` : null);
  const d = r.data;
  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <div className="no-print flex items-center justify-between">
        <p className="text-sm text-slate-500">Uma página para a reunião de diretoria. Troque a competência no topo.</p>
        <Button variant="primary" onClick={() => window.print()} disabled={!d}>
          Gerar PDF
        </Button>
      </div>
      <DataState isLoading={r.isLoading && !d} error={r.error}>
        {d && (
          <>
            <div className="border-b-2 border-gold-400 pb-2">
              <h1 className="text-2xl font-semibold text-slate-900">Relatório Mensal da Diretoria</h1>
              <p className="text-sm text-slate-500">
                {d.tenant} · Competência {fmtCompetencia(d.competencia)} · gerado em {new Date().toLocaleDateString("pt-BR")}
              </p>
            </div>

            {d.completude?.status === "preliminar" && (
              <div className="print-avoid rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                <StatusBadge tone="warning">Competência Preliminar</StatusBadge> {fmtPct(d.completude.completude_pct, 0)} das contas recebidas.
                Estimativa final da despesa {fmtBRLCompact(d.completude.despesa_liquida_estimada_final)} (+
                {fmtBRLCompact(d.completude.ibnr_estimado)} a chegar)
                {d.completude.sinistralidade_projetada != null && <>; sinistralidade projetada {fmtPct(d.completude.sinistralidade_projetada)}</>}.
              </div>
            )}

            <div className="print-avoid grid grid-cols-2 gap-3 md:grid-cols-4">
              <Kpi label="Sinistralidade" valor={fmtPct(d.kpis.sinistralidade)} sub={`${fmtPP(d.kpis.variacao_pp)} vs mês anterior`} />
              <Kpi label="Sinistralidade 12 meses" valor={fmtPct(d.kpis.acumulado_12m)} sub={d.kpis.variacao_pp_ano_anterior != null ? `${fmtPP(d.kpis.variacao_pp_ano_anterior)} vs ano anterior` : ""} />
              <Kpi label="Despesa assistencial" valor={fmtBRLCompact(d.kpis.despesa)} sub={`receita ${fmtBRLCompact(d.kpis.receita)}`} />
              <Kpi label="Custo por beneficiário" valor={fmtBRL(d.kpis.custo_assistencial_por_beneficiario, true)} sub={`${fmtNum(d.kpis.beneficiarios)} beneficiários`} />
            </div>

            <Card title="Evolução da Sinistralidade" className="print-avoid">
              <EvolutionChart serie={d.serie} competenciaAtual={d.competencia.slice(0, 7)} />
              {d.decomposicao_receita_despesa && (
                <p className="mt-2 text-xs text-slate-500">
                  Da variação do mês, {fmtPP(d.decomposicao_receita_despesa.efeito_despesa_pp)} vieram da despesa e{" "}
                  {fmtPP(d.decomposicao_receita_despesa.efeito_receita_pp)} da receita.
                </p>
              )}
            </Card>

            <div className="grid gap-4 md:grid-cols-2">
              {d.principais_fatores && (
                <Card title="Principais Fatores" className="print-avoid">
                  <ol className="list-decimal space-y-2 pl-5 text-sm">
                    {d.principais_fatores.map((i) => (
                      <li key={i.titulo}>
                        <span className="font-medium">{i.titulo}</span>
                        <div className="text-xs text-slate-500">{i.descricao}</div>
                      </li>
                    ))}
                  </ol>
                </Card>
              )}
              <div className="space-y-4">
                {d.alertas && (
                  <Card title="Alertas da Competência" className="print-avoid">
                    <div className="flex flex-wrap gap-2 text-sm">
                      <StatusBadge tone="danger">{d.alertas.por_severidade.critica ?? 0} críticos</StatusBadge>
                      <StatusBadge tone="warning">{d.alertas.por_severidade.atencao ?? 0} de atenção</StatusBadge>
                      <StatusBadge tone="info">{d.alertas.por_severidade.informativo ?? 0} informativos</StatusBadge>
                    </div>
                  </Card>
                )}
                {d.mercado && (
                  <Card title={`Mercado · ${d.mercado.segmento_rotulo}`} className="print-avoid">
                    {d.mercado.ilustrativo && <p className="mb-2 text-xs text-amber-800">{d.mercado.aviso}</p>}
                    {d.mercado.itens.map((i) => (
                      <div key={i.indicador} className="mb-3">
                        <div className="flex justify-between text-sm">
                          <span>{i.rotulo}</span>
                          <span className="font-medium">
                            {i.indicador === "sinistralidade_pct" ? fmtPct(i.operadora) : fmtBRL(i.operadora, true)}
                          </span>
                        </div>
                        <Regua item={i} />
                        <div className="text-xs text-slate-500">{i.posicao_rotulo}</div>
                      </div>
                    ))}
                  </Card>
                )}
              </div>
            </div>

            {d.planos_acao && (
              <Card title="Planos de Ação" className="print-avoid">
                <p className="mb-2 text-sm text-slate-600">
                  {d.planos_acao.total} plano(s) ·{" "}
                  {Object.entries(d.planos_acao.por_status).map(([k, v]) => `${v} ${STATUS_PLANO[k]?.toLowerCase() ?? k}`).join(" · ")}
                  {d.planos_acao.atrasados > 0 && <> · <strong className="text-rose-700">{d.planos_acao.atrasados} atrasado(s)</strong></>}
                </p>
                {d.planos_acao.destaque.length > 0 && (
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-slate-100 text-left text-xs uppercase text-slate-400">
                        <th className="py-1">Ação</th>
                        <th className="py-1">Responsável</th>
                        <th className="py-1">Status</th>
                        <th className="py-1 text-right">Variação do alvo</th>
                        <th className="py-1 text-right">vs carteira</th>
                      </tr>
                    </thead>
                    <tbody>
                      {d.planos_acao.destaque.map((p) => (
                        <tr key={p.titulo} className="border-b border-slate-50">
                          <td className="py-1">{p.titulo}<div className="text-xs text-slate-400">{p.alvo}</div></td>
                          <td className="py-1">{p.responsavel}</td>
                          <td className="py-1">{STATUS_PLANO[p.status] ?? p.status}</td>
                          <td className="py-1 text-right tabular-nums">{p.situacao_efeito === "medido" ? fmtSignedPct(p.variacao_alvo_pct) : "aguardando"}</td>
                          <td className="py-1 text-right tabular-nums">{p.efeito_relativo_pp != null ? fmtPP(p.efeito_relativo_pp) : "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </Card>
            )}

            <ul className="list-disc space-y-0.5 pl-5 text-xs text-slate-400">
              {d.notas.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          </>
        )}
      </DataState>
    </div>
  );
}

function Kpi({ label, valor, sub }: { label: string; valor: string; sub?: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-3">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-400">{label}</div>
      <div className="mt-1 text-xl font-semibold text-slate-900">{valor}</div>
      {sub && <div className="text-xs text-slate-500">{sub}</div>}
    </div>
  );
}
