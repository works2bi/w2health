"use client";

import { useEffect, useState } from "react";

import { Button, Card, DataState, Field, PageHeader, Select, StatusBadge, Toggle } from "@/components/ui";
import { apiSend } from "@/lib/api";
import { useFilters } from "@/lib/filters";
import { fmtCompetencia } from "@/lib/format";
import { useSession } from "@/lib/session";
import { useApi } from "@/lib/useApi";

interface Assinatura {
  id: number;
  email: string;
  frequencia: "semanal" | "mensal";
  severidade_minima: "critica" | "atencao" | "informativo";
  incluir_insights: boolean;
  ativo: boolean;
}

interface Previa {
  total: number;
  assunto: string;
  html: string;
  alertas_beneficiario_agregados: number;
}

interface Envio {
  id: number;
  competencia: string;
  destinatario: string;
  assunto: string;
  status: string;
  erro: string | null;
  enviado_em: string | null;
}

const TOM_ENVIO: Record<string, "success" | "info" | "danger" | "neutral"> = {
  enviado: "success",
  simulado: "info",
  falhou: "danger",
  vazio: "neutral",
};

export default function AlertasEmailPage() {
  const f = useFilters();
  const { can } = useSession();
  const minha = useApi<{ assinatura: Assinatura | null; email: string; transporte: string }>("/notificacoes/minha-assinatura");
  const [form, setForm] = useState({ frequencia: "semanal", severidade_minima: "atencao", incluir_insights: true, ativo: true });
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => {
    const a = minha.data?.assinatura;
    if (a) setForm({ frequencia: a.frequencia, severidade_minima: a.severidade_minima, incluir_insights: a.incluir_insights, ativo: a.ativo });
  }, [minha.data]);

  const previa = useApi<Previa>(
    f.competencia
      ? `/notificacoes/previa?competencia=${f.competencia}&severidade_minima=${form.severidade_minima}&incluir_insights=${form.incluir_insights}&frequencia=${form.frequencia}`
      : null,
  );
  const historico = useApi<{ itens: Envio[] }>(can("alert_rules:read") ? "/notificacoes/envios?limite=20" : null);

  const salvar = async () => {
    await apiSend("/notificacoes/minha-assinatura", "PUT", form);
    setMsg("Assinatura salva.");
    minha.reload();
  };
  const enviar = async () => {
    const r = await apiSend<Record<string, number>>(
      `/notificacoes/enviar?frequencia=${form.frequencia}${f.competencia ? `&competencia=${f.competencia}` : ""}`,
      "POST",
    );
    setMsg(`Envio: ${r.enviados} enviado(s), ${r.simulados} simulado(s), ${r.ignorados} já enviado(s) nesta janela, ${r.falhas} falha(s).`);
    historico.reload();
  };

  return (
    <div className="space-y-5">
      <PageHeader
        title="Alertas por E-mail"
        description="Receba um resumo dos alertas e insights da competência. Por privacidade, a mensagem nunca traz beneficiário identificado — o detalhe fica no W2Health."
      />
      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="Minha Assinatura">
          <DataState isLoading={minha.isLoading && !minha.data} error={minha.error}>
            <div className="space-y-3">
              <p className="text-sm text-slate-600">
                Destinatário: <strong>{minha.data?.email}</strong>
                {minha.data?.transporte === "simulado" && (
                  <> <StatusBadge tone="warning">Envio simulado</StatusBadge> <span className="text-xs">(SMTP não configurado neste ambiente)</span></>
                )}
              </p>
              <Field label="Frequência">
                {(id) => (
                  <Select id={id} value={form.frequencia} onChange={(e) => setForm({ ...form, frequencia: e.target.value })}>
                    <option value="semanal">Semanal</option>
                    <option value="mensal">Mensal</option>
                  </Select>
                )}
              </Field>
              <Field label="Severidade mínima">
                {(id) => (
                  <Select id={id} value={form.severidade_minima} onChange={(e) => setForm({ ...form, severidade_minima: e.target.value })}>
                    <option value="critica">Só críticos</option>
                    <option value="atencao">Atenção e críticos</option>
                    <option value="informativo">Tudo</option>
                  </Select>
                )}
              </Field>
              <div className="flex items-center gap-2 text-sm">
                <Toggle checked={form.incluir_insights} onChange={(v) => setForm({ ...form, incluir_insights: v })} label="Incluir insights" />
                Incluir insights automáticos
              </div>
              <div className="flex items-center gap-2 text-sm">
                <Toggle checked={form.ativo} onChange={(v) => setForm({ ...form, ativo: v })} label="Ativa" />
                Assinatura ativa
              </div>
              <div className="flex flex-wrap gap-2">
                <Button variant="primary" onClick={salvar}>Salvar</Button>
                {can("alert_rules:write") && <Button onClick={enviar}>Enviar Agora ({form.frequencia})</Button>}
              </div>
              {msg && <p className="text-sm text-emerald-700">{msg}</p>}
            </div>
          </DataState>
        </Card>
        <Card title="Prévia da Mensagem">
          <DataState isLoading={previa.isLoading && !previa.data} error={previa.error}>
            {previa.data && (
              <div className="space-y-2">
                <p className="text-xs text-slate-500">Assunto: {previa.data.assunto}</p>
                <iframe
                  title="Prévia do e-mail"
                  sandbox=""
                  srcDoc={previa.data.html}
                  className="h-96 w-full rounded-lg border border-slate-200 bg-white"
                />
              </div>
            )}
          </DataState>
        </Card>
      </div>
      {can("alert_rules:read") && (
        <Card title="Histórico de Envios">
          <DataState isLoading={historico.isLoading && !historico.data} error={historico.error} empty={!historico.data?.itens.length}>
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-100 text-left text-xs uppercase text-slate-400">
                  <th className="py-2">Quando</th>
                  <th className="py-2">Competência</th>
                  <th className="py-2">Destinatário</th>
                  <th className="py-2">Assunto</th>
                  <th className="py-2">Status</th>
                </tr>
              </thead>
              <tbody>
                {(historico.data?.itens ?? []).map((e) => (
                  <tr key={e.id} className="border-b border-slate-50">
                    <td className="py-1.5">{e.enviado_em ? new Date(e.enviado_em).toLocaleString("pt-BR") : "—"}</td>
                    <td className="py-1.5">{fmtCompetencia(e.competencia)}</td>
                    <td className="py-1.5">{e.destinatario}</td>
                    <td className="py-1.5">{e.assunto}</td>
                    <td className="py-1.5"><StatusBadge tone={TOM_ENVIO[e.status] ?? "neutral"} title={e.erro ?? undefined}>{e.status}</StatusBadge></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </DataState>
        </Card>
      )}
    </div>
  );
}
