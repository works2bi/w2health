"use client";


import { MiniSeries } from "@/components/charts";
import { Card, DataState, Stat } from "@/components/ui";
import { filtersQuery, useFilters } from "@/lib/filters";
import { fmtBRL, fmtBRLCompact, fmtNum, fmtPct } from "@/lib/format";
import { useApi } from "@/lib/useApi";
import { useRouteId } from "@/lib/useRouteId";

interface Detalhe {
  beneficiario: {
    id: number; codigo: string; sexo: string; idade: number; faixa_etaria: string;
    regiao: string; plano: string; contrato: string; status: string;
  };
  resumo: { despesa_total: number; eventos: number; meses_com_evento: number; custo_medio_evento: number };
  evolucao_mensal: { competencia: string; despesa: number; eventos: number }[];
  comportamento: {
    competencia_referencia: string;
    recorrencia: { meses_com_evento: number; janela_meses: number; classificacao: string };
    participacao_variacao: { participacao_pct: number; delta_beneficiario: number };
    eventos_pontuais_alto_custo: { competencia: string; procedimento: string; despesa_liquida: number }[];
  } | null;
  eventos: {
    id: number; data: string; competencia: string; tipo_atendimento: string; procedimento: string;
    especialidade: string; prestador: string; diagnostico: string | null; valor_pago: number;
  }[];
}
interface Timeline {
  timeline: { data: string; etapa: string; ordem_jornada: number; procedimento: string; especialidade: string; prestador: string; diagnostico: string | null; valor_pago: number }[];
}

const ETAPA_COR: Record<string, string> = {
  Consulta: "bg-sky-100 text-sky-800",
  Exame: "bg-violet-100 text-violet-800",
  "Pronto-socorro": "bg-amber-100 text-amber-800",
  Diagnóstico: "bg-teal-100 text-teal-800",
  Terapia: "bg-emerald-100 text-emerald-800",
  Procedimento: "bg-brand-100 text-brand-800",
  Internação: "bg-rose-100 text-rose-800",
};

const LABEL_RECORRENCIA: Record<string, string> = {
  esporadico: "Esporádico",
  recorrente: "Recorrente",
  utilizador_frequente: "Utilizador frequente",
};

export default function BeneficiarioDetalhePage() {
  const id = useRouteId();
  const f = useFilters();
  const d = useApi<Detalhe>(`/analytics/beneficiarios/${id}${f.competencia ? `?competencia=${f.competencia}` : ""}`);
  const tl = useApi<Timeline>(`/analytics/beneficiarios/${id}/timeline`);

  return (
    <DataState isLoading={d.isLoading && !d.data} error={d.error}>
      {d.data && (
        <div className="space-y-5">
          <div>
            <h2 className="font-mono text-lg font-semibold text-slate-900">{d.data.beneficiario.codigo}</h2>
            <p className="text-sm text-slate-500">
              {d.data.beneficiario.sexo} · {d.data.beneficiario.idade} anos ({d.data.beneficiario.faixa_etaria}) ·{" "}
              {d.data.beneficiario.regiao} · {d.data.beneficiario.plano} · {d.data.beneficiario.status}
            </p>
            <p className="mt-1 text-xs text-slate-400">Identificador sintético — nenhum dado de pessoa real.</p>
          </div>

          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Despesa total (histórico)" value={fmtBRLCompact(d.data.resumo.despesa_total)} />
            <Stat label="Eventos" value={fmtNum(d.data.resumo.eventos)} />
            <Stat label="Meses com evento" value={fmtNum(d.data.resumo.meses_com_evento)} />
            <Stat label="Custo médio por evento" value={fmtBRL(d.data.resumo.custo_medio_evento, true)} />
          </div>

          <Card title="Evolução mensal de custo">
            <MiniSeries serie={d.data.evolucao_mensal} dataKey="despesa" />
          </Card>

          {d.data.comportamento && (
            <Card title="Comportamento (v1.2)">
              <div className="flex flex-wrap gap-2 text-xs">
                <span className="rounded-full bg-slate-100 px-2 py-1 text-slate-700">
                  {LABEL_RECORRENCIA[d.data.comportamento.recorrencia.classificacao] ??
                    d.data.comportamento.recorrencia.classificacao}{" "}
                  · {d.data.comportamento.recorrencia.meses_com_evento}/
                  {d.data.comportamento.recorrencia.janela_meses} meses com evento
                </span>
                <span className="rounded-full bg-brand-50 px-2 py-1 text-brand-700">
                  {fmtPct(d.data.comportamento.participacao_variacao.participacao_pct)} da variação da carteira
                </span>
                {d.data.comportamento.eventos_pontuais_alto_custo.length > 0 && (
                  <span className="rounded-full bg-amber-50 px-2 py-1 text-amber-800">
                    {d.data.comportamento.eventos_pontuais_alto_custo.length} evento(s) pontual(is) de alto custo
                  </span>
                )}
              </div>
              <p className="mt-2 text-[11px] text-slate-400">
                Descritores observados (nunca previsão nem score clínico) — referência{" "}
                {d.data.comportamento.competencia_referencia}.
              </p>
            </Card>
          )}

          <Card title="Timeline assistencial simplificada">
            <DataState isLoading={tl.isLoading && !tl.data} error={tl.error}>
              <ol className="relative space-y-3 border-l-2 border-slate-200 pl-4">
                {(tl.data?.timeline ?? []).map((e, i) => (
                  <li key={i} className="relative">
                    <span className="absolute -left-[21px] top-1 h-3 w-3 rounded-full border-2 border-white bg-brand-500" />
                    <div className="flex flex-wrap items-baseline gap-2">
                      <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${ETAPA_COR[e.etapa] ?? "bg-slate-100 text-slate-700"}`}>
                        {e.etapa}
                      </span>
                      <span className="text-xs text-slate-400">{e.data}</span>
                      <span className="text-sm text-slate-800">{e.procedimento}</span>
                      <span className="text-xs text-slate-500">· {e.prestador}</span>
                      {e.diagnostico && <span className="text-xs text-slate-400">· {e.diagnostico}</span>}
                      <span className="ml-auto text-xs tabular-nums text-slate-600">{fmtBRLCompact(e.valor_pago)}</span>
                    </div>
                  </li>
                ))}
              </ol>
            </DataState>
          </Card>

          <Card title={`Eventos assistenciais (${d.data.eventos.length})`}>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[760px] text-sm">
                <thead>
                  <tr className="border-b border-slate-100 text-left text-xs uppercase text-slate-400">
                    <th className="py-2">Data</th>
                    <th className="py-2">Tipo</th>
                    <th className="py-2">Procedimento</th>
                    <th className="py-2">Prestador</th>
                    <th className="py-2">Diagnóstico</th>
                    <th className="py-2 text-right">Valor pago</th>
                  </tr>
                </thead>
                <tbody>
                  {d.data.eventos.map((e) => (
                    <tr key={e.id} className="border-b border-slate-50">
                      <td className="py-1.5 text-slate-500">{e.data}</td>
                      <td className="py-1.5 capitalize text-slate-500">{e.tipo_atendimento.replace("_", " ")}</td>
                      <td className="py-1.5">{e.procedimento}</td>
                      <td className="py-1.5 text-slate-500">{e.prestador}</td>
                      <td className="py-1.5 text-slate-400">{e.diagnostico ?? "—"}</td>
                      <td className="py-1.5 text-right tabular-nums">{fmtBRLCompact(e.valor_pago)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </div>
      )}
    </DataState>
  );
}
