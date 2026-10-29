"use client";

import { FormEvent, useState } from "react";

import { Button, Card, Field, Input, StatusBadge } from "@/components/ui";
import { apiForm } from "@/lib/api";

interface Relatorio {
  arquivos: { arquivo: string; padrao: string; eventos?: number; beneficiarios?: number; data_apresentacao?: string }[];
  contagens: Record<string, number>;
  valor_apresentado_total: string;
  competencias_apresentacao: string[];
  total_rejeitados: number;
  rejeitados: { arquivo: string; motivo: string; guia?: string; beneficiario?: string }[];
  beneficiarios_sem_cadastro_sib: number;
  prestadores_sem_uf: number;
  limitacoes: string[];
}

interface LinhaDiops {
  conta: string;
  descricao: string;
  diops: string | null;
  w2health: string;
  diferenca: string | null;
  diferenca_pct: number | null;
  status: "ok" | "divergente" | "ausente_no_diops";
}

const brl = (s: string | null) => (s == null ? "—" : Number(s).toLocaleString("pt-BR", { style: "currency", currency: "BRL" }));

/**
 * Item 6 — padrões ANS. TISS (guias) + SIB (beneficiários) viram o pacote canônico
 * (`ans_padroes_v1`) e seguem o pipeline normal; DIOPS é só conferência.
 */
export function PadroesAns({ base, fontes, onDone }: {
  base: string;
  fontes: { id: number; name: string }[];
  onDone: () => void;
}) {
  const [fonte, setFonte] = useState<number | null>(null);
  const [tiss, setTiss] = useState<FileList | null>(null);
  const [sib, setSib] = useState<FileList | null>(null);
  const [uf, setUf] = useState("");
  const [rel, setRel] = useState<Relatorio | null>(null);
  const [carga, setCarga] = useState<Record<string, unknown> | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const alvo = fonte ?? fontes[0]?.id ?? null;

  async function enviar(processar: boolean) {
    if (!alvo || !tiss?.length || !sib?.length) return;
    const fd = new FormData();
    Array.from(tiss).forEach((f) => fd.append("tiss", f));
    Array.from(sib).forEach((f) => fd.append("sib", f));
    fd.append("processar", String(processar));
    if (uf) fd.append("uf_padrao", uf.toUpperCase());
    setErro(null);
    setOcupado(true);
    try {
      const r = await apiForm<{ relatorio: Relatorio; carga: Record<string, unknown> | null }>(
        `${base}/sources/${alvo}/padroes-ans`, fd);
      setRel(r.relatorio);
      setCarga(r.carga);
      if (processar) onDone();
    } catch (e) {
      setErro((e as Error).message);
    } finally {
      setOcupado(false);
    }
  }

  return (
    <Card title="Padrões ANS — TISS, SIB e DIOPS">
      {fontes.length === 0 ? (
        <p className="text-sm text-slate-400">
          Cadastre uma fonte FILE com <code>{`{"mapping_id": "ans_padroes", "mapping_version": 1}`}</code> para importar TISS + SIB.
        </p>
      ) : (
        <div className="space-y-3">
          <div className="flex flex-wrap items-end gap-3">
            <Field label="Fonte">
              {(id) => (
                <select id={id} className="rounded-md border border-slate-300 px-2 py-2 text-sm" value={alvo ?? ""} onChange={(e) => setFonte(Number(e.target.value))}>
                  {fontes.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
                </select>
              )}
            </Field>
            <Field label="Lotes TISS (XML)">
              {(id) => <input id={id} type="file" multiple accept=".xml,text/xml" onChange={(e) => setTiss(e.target.files)} className="text-sm" />}
            </Field>
            <Field label="SIB — beneficiários (XML)">
              {(id) => <input id={id} type="file" multiple accept=".xml,text/xml" onChange={(e) => setSib(e.target.files)} className="text-sm" />}
            </Field>
            <Field label="UF padrão da rede" hint="Só para prestador sem beneficiário com UF.">
              {(id) => <Input id={id} maxLength={2} className="!w-20" value={uf} onChange={(e) => setUf(e.target.value)} />}
            </Field>
          </div>
          <div className="flex gap-2">
            <Button onClick={() => enviar(false)} loading={ocupado} disabled={!tiss?.length || !sib?.length}>Converter e conferir</Button>
            <Button variant="primary" onClick={() => enviar(true)} loading={ocupado} disabled={!rel || rel.prestadores_sem_uf > 0}>
              Processar carga
            </Button>
          </div>
        </div>
      )}
      {erro && <p className="mt-3 rounded-md bg-rose-50 px-3 py-2 text-sm text-rose-700">{erro}</p>}
      {rel && (
        <div className="mt-4 space-y-2 rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {Object.entries(rel.contagens).map(([k, v]) => (
              <div key={k} className="rounded bg-white px-2 py-1"><div className="text-[11px] text-slate-500">{k}</div><div className="font-semibold">{v}</div></div>
            ))}
          </div>
          <p>
            Valor apresentado: <strong>{brl(rel.valor_apresentado_total)}</strong> · competências de apresentação:{" "}
            {rel.competencias_apresentacao.join(", ") || "—"} · rejeitados: {rel.total_rejeitados}
            {rel.beneficiarios_sem_cadastro_sib > 0 && <> · <span className="text-amber-700">{rel.beneficiarios_sem_cadastro_sib} carteira(s) sem cadastro no SIB</span></>}
            {rel.prestadores_sem_uf > 0 && <> · <span className="text-rose-700">{rel.prestadores_sem_uf} prestador(es) sem UF — informe a UF padrão</span></>}
          </p>
          {rel.rejeitados.length > 0 && (
            <ul className="list-disc pl-5 text-xs text-rose-700">
              {rel.rejeitados.slice(0, 10).map((r, i) => <li key={i}>{r.arquivo} {r.guia ?? r.beneficiario ?? ""}: {r.motivo}</li>)}
            </ul>
          )}
          <ul className="list-disc pl-5 text-xs text-slate-500">{rel.limitacoes.map((l) => <li key={l}>{l}</li>)}</ul>
          {carga && <p className="text-xs">Carga enfileirada — ingestão #{String(carga.ingestion_run_id)} ({String(carga.status)}). Acompanhe nas execuções.</p>}
        </div>
      )}
      <Diops base={base} />
    </Card>
  );
}

function Diops({ base }: { base: string }) {
  const [arq, setArq] = useState<File | null>(null);
  const [ano, setAno] = useState(String(new Date().getFullYear()));
  const [tri, setTri] = useState("1");
  const [res, setRes] = useState<{ linhas: LinhaDiops[]; observacao: string; meses_com_dados: number } | null>(null);
  const [erro, setErro] = useState<string | null>(null);

  async function conferir(e: FormEvent) {
    e.preventDefault();
    if (!arq) return;
    const fd = new FormData();
    fd.append("arquivo", arq);
    fd.append("ano", ano);
    fd.append("trimestre", tri);
    setErro(null);
    try {
      setRes(await apiForm(`${base}/diops-conferencia`, fd));
    } catch (err) {
      setErro((err as Error).message);
    }
  }

  return (
    <div className="mt-6 border-t border-slate-100 pt-4">
      <h3 className="mb-2 text-sm font-semibold text-slate-800">Conferência com o DIOPS</h3>
      <form onSubmit={conferir} className="flex flex-wrap items-end gap-3">
        <Field label="Balancete (CSV conta;descricao;valor)">
          {(id) => <input id={id} type="file" accept=".csv,text/csv" onChange={(e) => setArq(e.target.files?.[0] ?? null)} className="text-sm" />}
        </Field>
        <Field label="Ano">{(id) => <Input id={id} type="number" className="!w-24" value={ano} onChange={(e) => setAno(e.target.value)} />}</Field>
        <Field label="Trimestre">
          {(id) => (
            <select id={id} className="rounded-md border border-slate-300 px-2 py-2 text-sm" value={tri} onChange={(e) => setTri(e.target.value)}>
              {[1, 2, 3, 4].map((t) => <option key={t} value={t}>{t}º</option>)}
            </select>
          )}
        </Field>
        <Button type="submit" disabled={!arq}>Conferir</Button>
      </form>
      {erro && <p className="mt-2 text-sm text-rose-700">{erro}</p>}
      {res && (
        <div className="mt-3 space-y-2 text-sm">
          <table className="w-full">
            <thead>
              <tr className="border-b border-slate-100 text-left text-xs uppercase text-slate-400">
                <th className="py-1">Conta</th><th className="py-1 text-right">DIOPS</th><th className="py-1 text-right">W2Health</th>
                <th className="py-1 text-right">Diferença</th><th className="py-1">Status</th>
              </tr>
            </thead>
            <tbody>
              {res.linhas.map((l) => (
                <tr key={l.conta} className="border-b border-slate-50">
                  <td className="py-1">{l.conta} — {l.descricao}</td>
                  <td className="py-1 text-right tabular-nums">{brl(l.diops)}</td>
                  <td className="py-1 text-right tabular-nums">{brl(l.w2health)}</td>
                  <td className="py-1 text-right tabular-nums">{brl(l.diferenca)}{l.diferenca_pct != null && ` (${l.diferenca_pct.toFixed(1)}%)`}</td>
                  <td className="py-1">
                    <StatusBadge tone={l.status === "ok" ? "success" : l.status === "divergente" ? "danger" : "neutral"}>{l.status}</StatusBadge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="text-xs text-slate-500">{res.observacao} Meses com dados no trimestre: {res.meses_com_dados}.</p>
        </div>
      )}
    </div>
  );
}
