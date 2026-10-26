"use client";

/**
 * Investigação da variação de custos (Evolução V1).
 *
 * Jornada única, embutida nas telas existentes (Sinistralidade, Contrato):
 * Indicador → Variação → Decomposição → Contribuições → Evidências → Investigação.
 * Tudo vem de GET /analytics/investigacao — nenhum número é calculado aqui; o painel só
 * apresenta valores, método e limitações devolvidos pelo backend.
 */

import Link from "next/link";
import { useState } from "react";

import { Badge, Card, DataState, PlanRestrictionNote } from "@/components/ui";
import { filtersQuery, useFilters } from "@/lib/filters";
import {
  corEvidencia,
  fmtBRLCompact,
  fmtCompetenciaCurta,
  fmtNum,
  fmtPct,
  fmtPP,
  fmtSignedPct,
  LABEL_EVIDENCIA,
} from "@/lib/format";
import { useSession } from "@/lib/session";
import { useApi } from "@/lib/useApi";

interface Efeito {
  codigo: string;
  rotulo: string;
  natureza: "isolado" | "nao_isolado" | "financeiro";
  unidade: string;
  valor: number | null;
  participacao_pct: number | null;
  disponivel: boolean;
  motivo: string;
}
interface Achado {
  tipo: "FATO" | "HIPOTESE" | "INVESTIGAR";
  codigo: string;
  titulo: string;
  texto: string;
  indicador: string;
  periodo: { base: string; atual: string };
  valor_referencia: number | null;
  valor_comparacao: number | null;
  contribuicao: { valor: number; participacao_pct: number | null } | null;
  evidencias: string[];
  limitacoes: string[];
  proximo_passo: string | null;
  metodologia: string;
}
interface Comparacao {
  base: string;
  disponivel: boolean;
  valor_referencia?: number;
  desvio_pct?: number | null;
  inicio?: string;
  fim?: string;
}
interface Historico {
  disponivel: boolean;
  valor_atual?: number;
  periodo_anterior?: Comparacao;
  ano_anterior?: Comparacao;
  media_historica?: { disponivel: boolean; n_competencias: number; media?: number; desvio_pct?: number | null; desvio_padronizado?: number | null };
  classificacao?: string;
  limitacoes?: string[];
  metodo?: string;
}
interface Investigacao {
  escopo: { rotulo: string };
  periodos: { atual: { inicio: string; fim: string }; base: { inicio: string; fim: string }; meses: number };
  dados_suficientes: boolean;
  sinistralidade?: {
    disponivel: boolean;
    motivo: string | null;
    s0?: number;
    s1?: number;
    variacao_pp?: number;
    efeito_despesa_pp?: number;
    efeito_receita_pp?: number;
  };
  resumo: { o_que_aconteceu: string; quanto_mudou: { valor: number; percentual: number | null }; fatores: string[]; onde: string[]; investigar: string[] };
  variacao: Record<string, number | null>;
  decomposicao: {
    efeitos: Efeito[];
    reconciliacao: { soma_efeitos: number; delta_observado: number; ok: boolean };
    por_procedimento: { chave: string; procedimento: string; delta: number; comparavel: boolean; custo_medio_anterior: number; custo_medio_atual: number; eventos_anterior: number; eventos_atual: number; efeitos: Record<string, number> }[];
    por_prestador: { chave: string; prestador: string | null; tipo: string | null; delta: number; efeitos: Record<string, number> }[];
  };
  historico: Record<string, Historico>;
  rede: {
    transferencias: { procedimento: string; efeito_mix_prestadores: number; movimentos: { id_prestador: number; prestador: string; participacao_anterior: number; participacao_atual: number; custo_medio_atual: number | null; situacao: string }[] }[];
    acima_dos_pares: { procedimento: string; id_prestador: number; prestador: string; custo_medio: number; mediana_pares: number; razao: number; n_pares: number }[];
    nota: string;
  } | null;
  coortes: { disponivel: boolean; motivo?: string; coortes?: { codigo: string; rotulo: string; n_beneficiarios: number; delta: number; participacao_variacao: number; despesa_anterior: number; despesa_atual: number }[]; criterios?: Record<string, string>; nota?: string };
  defasagem: { delta_defasada: number; delta_mesmo_mes: number };
  precos: { pares_com_tabela: number; cobertura_despesa: number; mudancas_de_tabela: { procedimento: string; prestador: string | null; valor_anterior: number | null; valor_novo: number; variacao_pct: number | null; vigencia_inicio: string }[] };
  unidades: { tipo_atendimento: string; unidade: string; eventos_anterior: number; eventos_atual: number; variacao_eventos_pct: number | null }[];
  cobertura_dados: { fator: string; dado: string; disponivel: boolean; cobertura: number | null; observacao: string }[];
  achados: Achado[];
  metodologia: Record<string, string>;
  restricoes_plano?: string[];
}

const JANELAS = [1, 3, 6, 12];
const ABAS = ["Onde", "Histórico", "Coortes", "Rede", "Preços", "Dados e método"] as const;
const CLASSE_HIST: Record<string, { label: string; tone: string }> = {
  excepcional: { label: "Excepcional vs histórico", tone: "bg-rose-50 text-rose-700 border-rose-200" },
  dentro_do_padrao: { label: "Dentro do padrão histórico", tone: "bg-emerald-50 text-emerald-700 border-emerald-200" },
  sazonal: { label: "Explicado pela sazonalidade", tone: "bg-sky-50 text-sky-700 border-sky-200" },
  indeterminado: { label: "Histórico insuficiente", tone: "bg-slate-50 text-slate-600 border-slate-200" },
};

const periodo = (p: { inicio: string; fim: string }) =>
  p.inicio === p.fim ? fmtCompetenciaCurta(p.inicio) : `${fmtCompetenciaCurta(p.inicio)}–${fmtCompetenciaCurta(p.fim)}`;

export function InvestigacaoPanel({
  dimensao,
  chave,
  contratoId,
  empresaId,
  titulo,
}: {
  dimensao?: string;
  chave?: string;
  /** escopo de contrato fixo (tela do contrato); senão usa o filtro global */
  contratoId?: string;
  /** escopo de empresa (contratos da empresa contratante em cada competência) */
  empresaId?: string;
  titulo?: string;
}) {
  const f = useFilters();
  const sessao = useSession();
  const [meses, setMeses] = useState(f.comparacao === "acumulado_12m" ? 12 : 1);
  const [aba, setAba] = useState<(typeof ABAS)[number]>("Onde");
  const comparacao = f.comparacao === "ano_anterior" ? "ano_anterior" : "mes_anterior";
  const extra: Record<string, string> = { meses: String(meses), comparacao };
  if (dimensao && chave) Object.assign(extra, { dimensao, chave });
  const ctr = contratoId ?? f.contratoId;
  if (empresaId) extra.empresa_id = empresaId;
  else if (ctr) extra.contrato_id = ctr;
  const permitido = sessao.hasFeature("advanced_explanations");
  const ready = f.competencia != null && permitido;
  const { data, isLoading, error } = useApi<Investigacao>(
    ready ? `/analytics/investigacao${filtersQuery(f, extra)}` : null,
  );
  if (!permitido) return null;

  return (
    <Card
      title={titulo ?? "Por que a despesa mudou?"}
      action={
        <div className="flex items-center gap-1 text-xs">
          <span className="text-slate-400">Janela</span>
          {JANELAS.map((m) => (
            <button
              key={m}
              onClick={() => setMeses(m)}
              className={`rounded-md border px-2 py-0.5 ${meses === m ? "border-brand-300 bg-brand-50 text-brand-800" : "border-slate-200 text-slate-500 hover:bg-slate-50"}`}
            >
              {m === 1 ? "1 mês" : `${m} meses`}
            </button>
          ))}
        </div>
      }
    >
      <DataState isLoading={isLoading && !data} error={error}>
        {data && <Conteudo d={data} aba={aba} setAba={setAba} />}
      </DataState>
    </Card>
  );
}

function Conteudo({ d, aba, setAba }: { d: Investigacao; aba: string; setAba: (a: (typeof ABAS)[number]) => void }) {
  const v = d.variacao;
  const hist = d.historico.despesa_liquida;
  const classe = CLASSE_HIST[hist?.classificacao ?? "indeterminado"] ?? CLASSE_HIST.indeterminado;
  return (
    <div className="space-y-5">
      {/* 1–2. O que aconteceu / quanto mudou */}
      <div>
        <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
          <span>{d.escopo.rotulo}</span>
          <span>·</span>
          <span>
            {periodo(d.periodos.base)} → {periodo(d.periodos.atual)}
          </span>
          <Badge className={classe.tone}>{classe.label}</Badge>
          {!d.dados_suficientes && (
            <Badge className="border-amber-300 bg-amber-50 text-amber-800">Dados insuficientes — sem hipóteses</Badge>
          )}
        </div>
        <p className="mt-2 text-sm text-slate-800">{d.resumo.o_que_aconteceu}</p>
        <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Mini label="Variação da despesa líquida" valor={fmtBRLCompact(d.resumo.quanto_mudou.valor)} sub={fmtSignedPct(d.resumo.quanto_mudou.percentual)} />
          <Mini label="Eventos" valor={`${fmtNum(v.eventos_anterior)} → ${fmtNum(v.eventos_atual)}`} sub={v.eventos_por_mil_atual != null ? `${(v.eventos_por_mil_anterior ?? 0).toFixed(0)} → ${(v.eventos_por_mil_atual ?? 0).toFixed(0)} por mil benef.-mês` : "sem exposição no escopo"} />
          <Mini label="Custo médio bruto por evento" valor={`${fmtBRLCompact(v.custo_medio_anterior)} → ${fmtBRLCompact(v.custo_medio_atual)}`} sub="mistura mix e preço — ver decomposição" />
          <Mini label="Glosa · coparticipação" valor={`${fmtBRLCompact(v.glosa_atual)} · ${fmtBRLCompact(v.coparticipacao_atual)}`} sub={`antes ${fmtBRLCompact(v.glosa_anterior)} · ${fmtBRLCompact(v.coparticipacao_anterior)}`} />
        </div>
      </div>

      {d.sinistralidade?.disponivel && (
        <div className="rounded-lg border border-brand-100 bg-brand-50 px-3 py-2 text-sm">
          <span className="font-medium text-brand-900">
            Sinistralidade do escopo: {fmtPct(d.sinistralidade.s0)} → {fmtPct(d.sinistralidade.s1)}
          </span>
          <span className="ml-2 text-xs text-slate-600">
            efeito despesa {fmtPP(d.sinistralidade.efeito_despesa_pp, 2)} · efeito receita{" "}
            {fmtPP(d.sinistralidade.efeito_receita_pp, 2)} (receita reconhecida atribuída ao escopo)
          </span>
        </div>
      )}
      {d.sinistralidade && !d.sinistralidade.disponivel && d.sinistralidade.motivo && (
        <p className="text-xs text-slate-500">Sinistralidade do escopo indisponível: {d.sinistralidade.motivo}.</p>
      )}

      {/* 3. Decomposição */}
      <Decomposicao efeitos={d.decomposicao.efeitos} rec={d.decomposicao.reconciliacao} />

      {/* 5–6. Evidências e investigação */}
      <div>
        <h3 className="mb-2 text-sm font-semibold text-slate-900">Conclusões — o que está comprovado e o que falta verificar</h3>
        <div className="space-y-2">
          {d.achados.map((a) => (
            <AchadoCard key={a.codigo} a={a} />
          ))}
        </div>
      </div>

      {/* 4. Onde + blocos de apoio */}
      <div>
        <div className="flex flex-wrap gap-1 border-b border-slate-100">
          {ABAS.map((x) => (
            <button
              key={x}
              onClick={() => setAba(x)}
              className={`-mb-px border-b-2 px-3 py-1.5 text-xs font-medium ${aba === x ? "border-brand-600 text-brand-800" : "border-transparent text-slate-500 hover:text-slate-700"}`}
            >
              {x}
            </button>
          ))}
        </div>
        <div className="pt-3">
          {aba === "Onde" && <Onde d={d} />}
          {aba === "Histórico" && <HistoricoView d={d} />}
          {aba === "Coortes" && <CoortesView c={d.coortes} />}
          {aba === "Rede" && <RedeView d={d} />}
          {aba === "Preços" && <PrecosView p={d.precos} />}
          {aba === "Dados e método" && <MetodoView d={d} />}
        </div>
      </div>
      <PlanRestrictionNote restricoes={d.restricoes_plano} />
    </div>
  );
}

function Mini({ label, valor, sub }: { label: string; valor: string; sub?: string }) {
  return (
    <div className="rounded-lg border border-slate-200 p-3">
      <div className="text-[11px] font-medium uppercase tracking-wide text-slate-400">{label}</div>
      <div className="mt-0.5 text-sm font-semibold text-slate-900">{valor}</div>
      {sub && <div className="text-[11px] text-slate-500">{sub}</div>}
    </div>
  );
}

function Decomposicao({ efeitos, rec }: { efeitos: Efeito[]; rec: Investigacao["decomposicao"]["reconciliacao"] }) {
  const calc = efeitos.filter((e) => e.valor != null);
  const indisp = efeitos.filter((e) => e.valor == null);
  const max = Math.max(1, ...calc.map((e) => Math.abs(e.valor ?? 0)));
  return (
    <div>
      <h3 className="text-sm font-semibold text-slate-900">Quanto cada fator contribuiu</h3>
      <p className="mb-2 text-xs text-slate-500">
        Contribuições somam exatamente a variação. Vermelho aumenta a despesa; verde reduz. Fatores tracejados
        não puderam ser isolados com os dados disponíveis.
      </p>
      <div className="space-y-1.5">
        {calc.map((e) => {
          const val = e.valor ?? 0;
          const w = `${(Math.abs(val) / max) * 100}%`;
          const cor = Math.abs(val) < 0.5 ? "bg-slate-200" : val > 0 ? "bg-rose-400" : "bg-emerald-400";
          const borda = e.natureza === "nao_isolado" ? "border border-dashed border-slate-400" : "";
          return (
            <div key={e.codigo} className="grid grid-cols-[minmax(0,14rem)_1fr_6.5rem] items-center gap-2 text-xs" title={e.unidade}>
              <span className={`truncate ${e.natureza === "financeiro" ? "text-slate-500" : "text-slate-700"}`}>{e.rotulo}</span>
              <div className="h-3 rounded bg-slate-50">
                <div className={`h-3 rounded ${cor} ${borda}`} style={{ width: w }} />
              </div>
              <span className="text-right tabular-nums text-slate-700">
                {fmtBRLCompact(val)}
                {e.participacao_pct != null && <span className="text-slate-400"> · {e.participacao_pct.toFixed(0)}%</span>}
              </span>
            </div>
          );
        })}
      </div>
      {indisp.length > 0 && (
        <ul className="mt-2 space-y-0.5 text-[11px] text-slate-500">
          {indisp.map((e) => (
            <li key={e.codigo}>
              <b>{e.rotulo}</b> — não calculado: {e.motivo}
            </li>
          ))}
        </ul>
      )}
      <p className="mt-2 text-[11px] text-slate-400">
        Reconciliação: soma dos efeitos {fmtBRLCompact(rec.soma_efeitos)} · variação observada{" "}
        {fmtBRLCompact(rec.delta_observado)} {rec.ok ? "✓" : "⚠"}
      </p>
    </div>
  );
}

function AchadoCard({ a }: { a: Achado }) {
  const [aberto, setAberto] = useState(false);
  const temDetalhe = a.evidencias.length > 0 || a.limitacoes.length > 0 || !!a.metodologia;
  return (
    <div className={`rounded-lg border px-3 py-2 text-sm ${corEvidencia(a.tipo)}`}>
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-wide">{LABEL_EVIDENCIA[a.tipo] ?? a.tipo}</span>
        <span className="font-medium">{a.titulo}</span>
        {a.contribuicao && (
          <span className="ml-auto tabular-nums text-xs">
            {fmtBRLCompact(a.contribuicao.valor)}
            {a.contribuicao.participacao_pct != null && ` · ${a.contribuicao.participacao_pct.toFixed(0)}% da variação`}
          </span>
        )}
      </div>
      <p className="mt-0.5 text-[13px] font-normal">{a.texto}</p>
      {a.proximo_passo && (
        <p className="mt-1 text-xs font-medium">→ {a.proximo_passo}</p>
      )}
      {temDetalhe && (
        <button className="mt-1 text-[11px] underline opacity-70 hover:opacity-100" onClick={() => setAberto(!aberto)}>
          {aberto ? "ocultar evidências" : "evidências, limitações e método"}
        </button>
      )}
      {aberto && (
        <div className="mt-1 space-y-1 text-xs font-normal">
          <div className="opacity-70">
            {a.indicador} · {a.periodo.base} → {a.periodo.atual}
          </div>
          {a.evidencias.length > 0 && (
            <ul className="list-disc pl-4">
              {a.evidencias.map((e, i) => (
                <li key={i}>{e}</li>
              ))}
            </ul>
          )}
          {a.limitacoes.length > 0 && (
            <div>
              <b>Limitações:</b> {a.limitacoes.join(" · ")}
            </div>
          )}
          {a.metodologia && <div className="opacity-70">Método: {a.metodologia}</div>}
        </div>
      )}
    </div>
  );
}

function Onde({ d }: { d: Investigacao }) {
  const nomeEfeito = (e: Record<string, number>) => {
    const ent = Object.entries(e).filter(([, x]) => Math.abs(x) > 0.5);
    if (!ent.length) return "—";
    const [k] = ent.sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))[0];
    return d.decomposicao.efeitos.find((x) => x.codigo === k)?.rotulo ?? (k === "volume" ? "Volume (população + frequência)" : k);
  };
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <div>
        <div className="mb-1 text-xs font-medium text-slate-500">Procedimentos que mais variaram</div>
        <table className="w-full text-xs">
          <tbody>
            {d.decomposicao.por_procedimento.slice(0, 8).map((p) => (
              <tr key={p.chave} className="border-b border-slate-50">
                <td className="py-1">
                  <Link href={`/sinistralidade?dimensao=procedimento&chave=${p.chave}`} className="text-brand-600 hover:underline">
                    {p.procedimento}
                  </Link>
                  <div className="text-[11px] text-slate-400">
                    {fmtNum(p.eventos_anterior)} → {fmtNum(p.eventos_atual)} eventos · custo médio {fmtBRLCompact(p.custo_medio_anterior)} → {fmtBRLCompact(p.custo_medio_atual)}
                  </div>
                </td>
                <td className="py-1 text-right tabular-nums">{fmtBRLCompact(p.delta)}</td>
                <td className="py-1 pl-2 text-right text-[11px] text-slate-500">{p.comparavel ? nomeEfeito(p.efeitos) : "sem base comparável"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div>
        <div className="mb-1 text-xs font-medium text-slate-500">Prestadores que mais variaram</div>
        {d.decomposicao.por_prestador.length === 0 ? (
          <p className="text-xs text-slate-400">Detalhe por prestador indisponível no plano contratado.</p>
        ) : (
          <table className="w-full text-xs">
            <tbody>
              {d.decomposicao.por_prestador.slice(0, 8).map((p) => (
                <tr key={p.chave} className="border-b border-slate-50">
                  <td className="py-1">
                    <Link href={`/prestadores/${p.chave}`} className="text-brand-600 hover:underline">
                      {p.prestador ?? p.chave}
                    </Link>
                    <span className="ml-1 text-[11px] text-slate-400">{p.tipo?.replace("_", " ")}</span>
                  </td>
                  <td className="py-1 text-right tabular-nums">{fmtBRLCompact(p.delta)}</td>
                  <td className="py-1 pl-2 text-right text-[11px] text-slate-500">{nomeEfeito(p.efeitos)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="mt-3 mb-1 text-xs font-medium text-slate-500">Volume por tipo de atendimento (cada um na sua unidade)</div>
        <table className="w-full text-xs">
          <tbody>
            {d.unidades.map((u) => (
              <tr key={u.tipo_atendimento} className="border-b border-slate-50">
                <td className="py-1 text-slate-600">{u.unidade}</td>
                <td className="py-1 text-right tabular-nums">{fmtNum(u.eventos_anterior)} → {fmtNum(u.eventos_atual)}</td>
                <td className="py-1 text-right tabular-nums text-slate-500">{fmtSignedPct(u.variacao_eventos_pct)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function HistoricoView({ d }: { d: Investigacao }) {
  const linhas: [string, Historico][] = [
    ["Despesa líquida mensal", d.historico.despesa_liquida],
    ["Eventos por mês", d.historico.eventos],
    ["Custo médio por evento", d.historico.custo_medio],
  ];
  const cmp = (c?: Comparacao) => (c && c.disponivel ? fmtSignedPct(c.desvio_pct ?? null) : "sem dados");
  return (
    <div className="space-y-2">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-left text-[11px] uppercase text-slate-400">
            <th className="py-1">Indicador</th>
            <th className="py-1 text-right">Atual (média mensal)</th>
            <th className="py-1 text-right">vs período anterior</th>
            <th className="py-1 text-right">vs ano anterior</th>
            <th className="py-1 text-right">vs média histórica</th>
            <th className="py-1 text-right">Classificação</th>
          </tr>
        </thead>
        <tbody>
          {linhas.map(([rot, h]) => (
            <tr key={rot} className="border-b border-slate-50">
              <td className="py-1 text-slate-600">{rot}</td>
              <td className="py-1 text-right tabular-nums">{h?.disponivel ? (rot.startsWith("Eventos") ? fmtNum(h.valor_atual) : fmtBRLCompact(h.valor_atual)) : "—"}</td>
              <td className="py-1 text-right tabular-nums">{cmp(h?.periodo_anterior)}</td>
              <td className="py-1 text-right tabular-nums">{cmp(h?.ano_anterior)}</td>
              <td className="py-1 text-right tabular-nums">
                {h?.media_historica?.disponivel
                  ? `${fmtSignedPct(h.media_historica.desvio_pct ?? null)} (${h.media_historica.n_competencias} comp.)`
                  : "insuficiente"}
              </td>
              <td className="py-1 text-right">{CLASSE_HIST[h?.classificacao ?? "indeterminado"]?.label ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {d.historico.despesa_liquida?.limitacoes?.length ? (
        <p className="text-[11px] text-slate-500">Limitações: {d.historico.despesa_liquida.limitacoes.join(" · ")}</p>
      ) : null}
      <p className="text-[11px] text-slate-400">{d.historico.despesa_liquida?.metodo}</p>
    </div>
  );
}

function CoortesView({ c }: { c: Investigacao["coortes"] }) {
  if (!c.disponivel) return <p className="text-xs text-slate-500">{c.motivo}</p>;
  return (
    <div className="space-y-2">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-left text-[11px] uppercase text-slate-400">
            <th className="py-1">Coorte de utilização</th>
            <th className="py-1 text-right">Beneficiários</th>
            <th className="py-1 text-right">Despesa base → atual</th>
            <th className="py-1 text-right">Δ</th>
            <th className="py-1 text-right">% da variação</th>
          </tr>
        </thead>
        <tbody>
          {(c.coortes ?? []).map((x) => (
            <tr key={x.codigo} className="border-b border-slate-50">
              <td className="py-1">
                {x.rotulo}
                <div className="text-[11px] text-slate-400">{c.criterios?.[x.codigo]}</div>
              </td>
              <td className="py-1 text-right tabular-nums">{fmtNum(x.n_beneficiarios)}</td>
              <td className="py-1 text-right tabular-nums">{fmtBRLCompact(x.despesa_anterior)} → {fmtBRLCompact(x.despesa_atual)}</td>
              <td className="py-1 text-right tabular-nums">{fmtBRLCompact(x.delta)}</td>
              <td className="py-1 text-right tabular-nums">{fmtPct(x.participacao_variacao, 0)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-[11px] text-slate-500">{c.nota}</p>
    </div>
  );
}

function RedeView({ d }: { d: Investigacao }) {
  if (!d.rede) return <p className="text-xs text-slate-400">Análise de rede indisponível no plano contratado.</p>;
  const r = d.rede;
  return (
    <div className="space-y-3">
      <div>
        <div className="mb-1 text-xs font-medium text-slate-500">Transferência de volume entre prestadores (mesmo procedimento)</div>
        {r.transferencias.length === 0 && <p className="text-xs text-slate-400">Sem mudança relevante de participação entre prestadores.</p>}
        {r.transferencias.map((t) => (
          <div key={t.procedimento} className="mb-2 rounded-md border border-slate-100 p-2 text-xs">
            <div className="font-medium text-slate-700">
              {t.procedimento} <span className="font-normal text-slate-400">· efeito rede {fmtBRLCompact(t.efeito_mix_prestadores)}</span>
            </div>
            {t.movimentos.map((m) => (
              <div key={m.id_prestador} className="flex justify-between">
                <Link href={`/prestadores/${m.id_prestador}`} className="text-brand-600 hover:underline">
                  {m.prestador}
                  {m.situacao !== "permanece" && <span className="ml-1 text-slate-400">({m.situacao})</span>}
                </Link>
                <span className="tabular-nums text-slate-600">
                  {fmtPct(m.participacao_anterior * 100, 0)} → {fmtPct(m.participacao_atual * 100, 0)} do volume · {fmtBRLCompact(m.custo_medio_atual)}
                </span>
              </div>
            ))}
          </div>
        ))}
      </div>
      {r.acima_dos_pares.length > 0 && (
        <div>
          <div className="mb-1 text-xs font-medium text-slate-500">Custo acima de pares comparáveis (mesmo procedimento, mesmo tipo)</div>
          <table className="w-full text-xs">
            <tbody>
              {r.acima_dos_pares.map((p) => (
                <tr key={`${p.procedimento}-${p.id_prestador}`} className="border-b border-slate-50">
                  <td className="py-1">
                    <Link href={`/prestadores/${p.id_prestador}`} className="text-brand-600 hover:underline">{p.prestador}</Link>
                    <span className="text-slate-400"> · {p.procedimento}</span>
                  </td>
                  <td className="py-1 text-right tabular-nums">
                    {fmtBRLCompact(p.custo_medio)} vs mediana {fmtBRLCompact(p.mediana_pares)} ({p.n_pares} pares)
                  </td>
                  <td className="py-1 pl-2 text-right font-medium tabular-nums text-amber-700">{p.razao.toFixed(2)}×</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="text-[11px] text-slate-500">{r.nota}</p>
    </div>
  );
}

function PrecosView({ p }: { p: Investigacao["precos"] }) {
  return (
    <div className="space-y-2 text-xs">
      <p className="text-slate-600">
        {p.pares_com_tabela} par(es) prestador × procedimento com tabela vigente nos dois períodos — cobrem{" "}
        {fmtPct(p.cobertura_despesa * 100, 0)} da despesa comparável. Sem tabela, aumento de valor por unidade é
        apresentado como &quot;custo unitário observado&quot;, nunca como reajuste.
      </p>
      {p.mudancas_de_tabela.length === 0 ? (
        <p className="text-slate-400">Nenhuma nova vigência de tabela no período.</p>
      ) : (
        <table className="w-full">
          <tbody>
            {p.mudancas_de_tabela.map((m, i) => (
              <tr key={i} className="border-b border-slate-50">
                <td className="py-1">{m.prestador ? `${m.prestador} · ` : ""}{m.procedimento}</td>
                <td className="py-1 text-right tabular-nums">
                  {fmtBRLCompact(m.valor_anterior)} → {fmtBRLCompact(m.valor_novo)} ({fmtSignedPct(m.variacao_pct)})
                </td>
                <td className="py-1 pl-2 text-right text-slate-500">desde {m.vigencia_inicio.split("-").reverse().join("/")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function MetodoView({ d }: { d: Investigacao }) {
  return (
    <div className="space-y-3 text-xs">
      <div>
        <div className="mb-1 font-medium text-slate-500">Cobertura dos dados neste escopo</div>
        <table className="w-full">
          <tbody>
            {d.cobertura_dados.map((c) => (
              <tr key={c.fator} className="border-b border-slate-50">
                <td className="py-1 text-slate-700">{c.fator}</td>
                <td className="py-1 text-slate-500">{c.dado}</td>
                <td className="py-1 text-right">
                  {c.disponivel ? (
                    <span className="text-emerald-700">{c.cobertura != null ? fmtPct(c.cobertura * 100, 0) : "disponível"}</span>
                  ) : (
                    <span className="text-slate-400">indisponível</span>
                  )}
                </td>
                <td className="py-1 pl-2 text-[11px] text-slate-400">{c.observacao}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <dl className="space-y-1 text-slate-600">
        {Object.entries(d.metodologia).map(([k, v]) => (
          <div key={k}>
            <dt className="inline font-medium capitalize">{k.replace("_", " ")}: </dt>
            <dd className="inline">{v}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
