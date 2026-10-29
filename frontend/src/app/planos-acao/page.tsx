"use client";

import { useSearchParams } from "next/navigation";
import { useState } from "react";

import { MiniSeries } from "@/components/charts";
import { Button, Card, DataState, Field, Input, PageHeader, Select, StatusBadge } from "@/components/ui";
import { apiSend } from "@/lib/api";
import { useFilters } from "@/lib/filters";
import { fmtBRLCompact, fmtCompetencia, fmtPP, fmtSignedPct, LABEL_DIMENSAO } from "@/lib/format";
import { useSession } from "@/lib/session";
import { useApi } from "@/lib/useApi";

interface Efeito {
  situacao: "medido" | "aguardando_dados" | "sem_linha_de_base";
  mensagem?: string;
  meses_antes: string[];
  meses_depois: string[];
  meses_preliminares_ignorados: string[];
  media_antes?: number;
  media_depois?: number;
  variacao_alvo_pct?: number | null;
  variacao_restante_pct?: number | null;
  efeito_relativo_pp?: number | null;
  atingiu_meta?: boolean | null;
  parcial?: boolean;
  leitura?: string;
  serie: { competencia: string; alvo: number; preliminar: boolean }[];
}

interface Plano {
  id: number;
  titulo: string;
  descricao: string;
  origem_tipo: string;
  alvo_rotulo: string;
  dimensao: string | null;
  responsavel: string;
  prazo: string | null;
  status: string;
  competencia_inicio: string;
  meta_reducao_pct: number | null;
  efeito: Efeito;
}

const STATUS: Record<string, { label: string; tone: "neutral" | "info" | "success" | "danger" }> = {
  aberto: { label: "Aberto", tone: "neutral" },
  em_andamento: { label: "Em andamento", tone: "info" },
  concluido: { label: "Concluído", tone: "success" },
  cancelado: { label: "Cancelado", tone: "danger" },
};

export default function PlanosAcaoPage() {
  const params = useSearchParams();
  const { can } = useSession();
  const pode = can("action_plans:write");
  const lista = useApi<{ total: number; itens: Plano[] }>("/planos-acao");
  const [novo, setNovo] = useState(params.get("novo") === "1");

  return (
    <div className="space-y-5">
      <PageHeader
        title="Planos de Ação"
        description="Do insight à ação: quem faz, até quando, e se a despesa do alvo mudou depois (variação = FATO; efeito da ação = HIPÓTESE)."
        actions={pode && !novo ? <Button variant="primary" onClick={() => setNovo(true)}>Novo Plano</Button> : null}
      />
      {novo && pode && <NovoPlano onDone={() => { setNovo(false); lista.reload(); }} />}
      <DataState isLoading={lista.isLoading && !lista.data} error={lista.error} empty={lista.data?.total === 0}>
        <div className="space-y-3">
          {(lista.data?.itens ?? []).map((p) => (
            <PlanoCard key={p.id} plano={p} pode={pode} onChange={() => lista.reload()} />
          ))}
        </div>
      </DataState>
    </div>
  );
}

function PlanoCard({ plano: p, pode, onChange }: { plano: Plano; pode: boolean; onChange: () => void }) {
  const e = p.efeito;
  const atrasado = p.prazo && new Date(p.prazo) < new Date() && (p.status === "aberto" || p.status === "em_andamento");
  const mudar = async (status: string) => {
    await apiSend(`/planos-acao/${p.id}`, "PATCH", { status });
    onChange();
  };
  return (
    <Card
      title={
        <span className="flex flex-wrap items-center gap-2">
          {p.titulo}
          <StatusBadge tone={STATUS[p.status]?.tone ?? "neutral"}>{STATUS[p.status]?.label ?? p.status}</StatusBadge>
          {atrasado && <StatusBadge tone="danger">Atrasado</StatusBadge>}
        </span>
      }
      action={
        pode ? (
          <Select className="!w-auto" value={p.status} onChange={(ev) => mudar(ev.target.value)} aria-label="Status">
            {Object.entries(STATUS).map(([k, v]) => (
              <option key={k} value={k}>{v.label}</option>
            ))}
          </Select>
        ) : null
      }
    >
      <div className="grid gap-4 md:grid-cols-3">
        <dl className="space-y-1 text-sm">
          <div><dt className="inline text-slate-400">Alvo: </dt><dd className="inline">{p.alvo_rotulo}</dd></div>
          <div><dt className="inline text-slate-400">Responsável: </dt><dd className="inline">{p.responsavel}</dd></div>
          <div><dt className="inline text-slate-400">Prazo: </dt><dd className="inline">{p.prazo ? new Date(`${p.prazo}T00:00`).toLocaleDateString("pt-BR") : "—"}</dd></div>
          <div><dt className="inline text-slate-400">Vale a partir de: </dt><dd className="inline">{fmtCompetencia(p.competencia_inicio)}</dd></div>
          {p.meta_reducao_pct != null && (
            <div><dt className="inline text-slate-400">Meta: </dt><dd className="inline">−{p.meta_reducao_pct}% na despesa do alvo</dd></div>
          )}
          {p.descricao && <p className="pt-1 text-slate-600">{p.descricao}</p>}
        </dl>
        <div className="md:col-span-2">
          {e.situacao !== "medido" ? (
            <p className="rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-600">{e.mensagem}</p>
          ) : (
            <div className="space-y-2">
              <div className="flex flex-wrap gap-4 text-sm">
                <span>Despesa média do alvo: {fmtBRLCompact(e.media_antes)} → <strong>{fmtBRLCompact(e.media_depois)}</strong> ({fmtSignedPct(e.variacao_alvo_pct)}) <StatusBadge tone="success">FATO</StatusBadge></span>
                {e.efeito_relativo_pp != null && (
                  <span>vs restante da carteira: <strong>{fmtPP(e.efeito_relativo_pp)}</strong> <StatusBadge tone="warning">HIPÓTESE</StatusBadge></span>
                )}
                {e.atingiu_meta != null && (
                  <StatusBadge tone={e.atingiu_meta ? "success" : "danger"}>{e.atingiu_meta ? "Meta atingida" : "Meta não atingida"}</StatusBadge>
                )}
                {e.parcial && <StatusBadge tone="neutral">Medição parcial</StatusBadge>}
              </div>
              <MiniSeries serie={e.serie.map((x) => ({ competencia: x.competencia, valor: x.alvo }))} dataKey="valor" />
              <p className="text-xs text-slate-500">{e.leitura}</p>
            </div>
          )}
          {e.meses_preliminares_ignorados.length > 0 && (
            <p className="mt-1 text-xs text-slate-400">
              Fora da medição (menos de 95% das contas recebidas): {e.meses_preliminares_ignorados.map(fmtCompetencia).join(", ")}.
            </p>
          )}
        </div>
      </div>
    </Card>
  );
}

function NovoPlano({ onDone }: { onDone: () => void }) {
  const params = useSearchParams();
  const f = useFilters();
  const [form, setForm] = useState({
    titulo: params.get("titulo") ?? "",
    descricao: params.get("descricao") ?? "",
    responsavel: "",
    prazo: "",
    competencia_inicio: `${params.get("competencia") ?? f.competencia ?? ""}`.slice(0, 7),
    meta_reducao_pct: "",
  });
  const dimensao = params.get("dimensao");
  const chave = params.get("chave");
  const origem = params.get("origem_ref");
  const [erro, setErro] = useState<string | null>(null);
  const set = (k: keyof typeof form) => (ev: { target: { value: string } }) => setForm({ ...form, [k]: ev.target.value });

  const salvar = async () => {
    setErro(null);
    try {
      await apiSend("/planos-acao", "POST", {
        titulo: form.titulo,
        descricao: form.descricao,
        responsavel: form.responsavel,
        prazo: form.prazo || null,
        competencia_inicio: `${form.competencia_inicio}-01`,
        meta_reducao_pct: form.meta_reducao_pct ? Number(form.meta_reducao_pct) : null,
        origem_tipo: origem ? "insight" : "manual",
        origem_ref: origem,
        dimensao: dimensao && chave ? dimensao : null,
        chave: dimensao && chave ? chave : null,
        alvo_rotulo: params.get("alvo") || null,
      });
      onDone();
    } catch (e) {
      setErro((e as Error).message);
    }
  };

  return (
    <Card title="Novo Plano de Ação">
      <div className="grid gap-3 md:grid-cols-2">
        <Field label="Título">{(id) => <Input id={id} value={form.titulo} onChange={set("titulo")} />}</Field>
        <Field label="Responsável">{(id) => <Input id={id} value={form.responsavel} onChange={set("responsavel")} />}</Field>
        <Field label="Prazo">{(id) => <Input id={id} type="date" value={form.prazo} onChange={set("prazo")} />}</Field>
        <Field label="Vale a partir da competência" hint="Antes = linha de base; depois = efeito.">
          {(id) => <Input id={id} type="month" value={form.competencia_inicio} onChange={set("competencia_inicio")} />}
        </Field>
        <Field label="Meta de redução da despesa do alvo (%)">
          {(id) => <Input id={id} type="number" min={0} max={100} value={form.meta_reducao_pct} onChange={set("meta_reducao_pct")} />}
        </Field>
        <Field label="Alvo da medição">
          {(id) => (
            <Input
              id={id}
              disabled
              value={dimensao && chave ? `${LABEL_DIMENSAO[dimensao] ?? dimensao}: ${params.get("alvo") ?? chave}` : "Carteira inteira"}
            />
          )}
        </Field>
        <div className="md:col-span-2">
          <Field label="Descrição">{(id) => <Input id={id} value={form.descricao} onChange={set("descricao")} />}</Field>
        </div>
      </div>
      {erro && <p className="mt-2 text-sm text-rose-600">{erro}</p>}
      <div className="mt-3 flex gap-2">
        <Button variant="primary" onClick={salvar} disabled={form.titulo.length < 3 || form.responsavel.length < 2 || !form.competencia_inicio}>
          Salvar
        </Button>
        <Button variant="secondary" onClick={onDone}>Cancelar</Button>
      </div>
    </Card>
  );
}
