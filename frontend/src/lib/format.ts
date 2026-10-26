/** Formatadores de exibição (pt-BR). */

const brl = new Intl.NumberFormat("pt-BR", {
  style: "currency",
  currency: "BRL",
  maximumFractionDigits: 0,
});
const brlCents = new Intl.NumberFormat("pt-BR", {
  style: "currency",
  currency: "BRL",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const num0 = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });

export const fmtBRL = (v: number | null | undefined, cents = false): string =>
  v == null ? "—" : (cents ? brlCents : brl).format(v);

export const fmtBRLCompact = (v: number | null | undefined): string => {
  if (v == null) return "—";
  const abs = Math.abs(v);
  // sempre com o prefixo da moeda (antes: "R$ 286,00" abaixo de mil, mas "34 mil" sem R$)
  const sinal = v < 0 ? "-" : "";
  if (abs >= 1_000_000) return `${sinal}R$ ${(abs / 1_000_000).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} mi`;
  if (abs >= 1_000) return `${sinal}R$ ${(abs / 1_000).toLocaleString("pt-BR", { maximumFractionDigits: 0 })} mil`;
  return brl.format(v);
};

export const fmtNum = (v: number | null | undefined): string => (v == null ? "—" : num0.format(v));

export const fmtPct = (v: number | null | undefined, digits = 1): string =>
  v == null ? "—" : `${v.toLocaleString("pt-BR", { minimumFractionDigits: digits, maximumFractionDigits: digits })}%`;

export const fmtPP = (v: number | null | undefined, digits = 1): string => {
  if (v == null) return "—";
  const s = v >= 0 ? "+" : "";
  return `${s}${v.toLocaleString("pt-BR", { minimumFractionDigits: digits, maximumFractionDigits: digits })} p.p.`;
};

export const fmtSignedPct = (v: number | null | undefined, digits = 1): string => {
  if (v == null) return "—";
  const s = v >= 0 ? "+" : "";
  return `${s}${v.toLocaleString("pt-BR", { minimumFractionDigits: digits, maximumFractionDigits: digits })}%`;
};

const MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];

/** "2026-07-01" -> "jul/26" */
export const fmtCompetenciaCurta = (iso: string): string => {
  const [y, m] = iso.split("-");
  return `${MESES[Number(m) - 1]}/${y.slice(2)}`;
};

/** "2026-07-01" -> "Julho/2026" */
export const fmtCompetencia = (iso: string): string => {
  const [y, m] = iso.split("-");
  const nome = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"][Number(m) - 1];
  return `${nome}/${y}`;
};

export const LABEL_COMPARACAO: Record<string, string> = {
  mes_anterior: "mês anterior",
  ano_anterior: "mesmo mês do ano anterior",
  acumulado_12m: "acumulado 12 meses",
};

export const LABEL_DIMENSAO: Record<string, string> = {
  grupo_despesa: "Grupo de despesa",
  tipo_atendimento: "Tipo de atendimento",
  especialidade: "Especialidade",
  procedimento: "Procedimento",
  prestador: "Prestador",
  regiao: "Região",
  faixa_etaria: "Faixa etária",
  sexo: "Sexo",
  plano: "Plano",
  contrato: "Contrato",
};

export const LABEL_EVIDENCIA: Record<string, string> = {
  FATO: "Fato",
  HIPOTESE: "Hipótese",
  A_INVESTIGAR: "A investigar",
  INVESTIGAR: "Investigar",
};

export const LABEL_CONFIANCA: Record<string, string> = {
  ALTA: "Confiança alta",
  MEDIA: "Confiança média",
  BAIXA: "Confiança baixa",
};

export const corEvidencia = (tipo: string): string =>
  ({
    FATO: "bg-slate-100 text-slate-800 border-slate-300",
    HIPOTESE: "bg-amber-50 text-amber-800 border-amber-300 border-dashed",
    A_INVESTIGAR: "bg-slate-50 text-slate-400 border-slate-200 border-dotted",
    INVESTIGAR: "bg-sky-50 text-sky-800 border-sky-300",
  })[tipo] ?? "bg-slate-100 text-slate-700 border-slate-200";

export const LABEL_EFEITO: Record<string, string> = {
  frequencia: "Frequência",
  custo_medio: "Custo médio",
  misto: "Misto",
};

export const corSeveridade = (sev: string): string =>
  ({
    alta: "bg-rose-100 text-rose-800 border-rose-200",
    media: "bg-amber-100 text-amber-800 border-amber-200",
    baixa: "bg-yellow-50 text-yellow-800 border-yellow-200",
    positiva: "bg-emerald-100 text-emerald-800 border-emerald-200",
    info: "bg-sky-100 text-sky-800 border-sky-200",
  })[sev] ?? "bg-slate-100 text-slate-700 border-slate-200";
