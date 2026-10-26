"use client";

/**
 * Composição da receita da competência (Consolidação do canônico V1).
 *
 * Mostra de onde veio a receita usada no indicador (fonte única: reconhecida OU legada por
 * plano — nunca a soma), a receita reconhecida por nível de atribuição e, à parte, faturado
 * e recebido — fatos diferentes, cada um na sua competência. Nenhum valor é calculado aqui.
 */

import { Card, DataState } from "@/components/ui";
import { filtersQuery, useFilters } from "@/lib/filters";
import { fmtBRLCompact, fmtPct } from "@/lib/format";
import { useSession } from "@/lib/session";
import { useApi } from "@/lib/useApi";

interface Composicao {
  receita_operadora: { valor: number; fonte: string; status: string; nota: string };
  receita_reconhecida: {
    total: number;
    niveis: { nivel: string; rotulo: string; valor: number; participacao: number | null; entra_na_sinistralidade_contratual: boolean }[];
    cobertura_contratual: number | null;
  };
  receita_legada_plano: number | null;
  faturamento: { total: number; por_unidade: { unidade_cobranca: string; documentos: number; total: number }[] };
  recebimento: { total: number };
  avisos: string[];
}

const FONTE: Record<string, string> = {
  reconhecida: "receita reconhecida",
  legada_plano: "receita legada por plano",
  sem_receita: "sem receita",
};

export function ReceitaCard() {
  const f = useFilters();
  const sessao = useSession();
  const permitido = sessao.hasFeature("financial_composition");
  const { data, isLoading, error } = useApi<Composicao>(
    f.competencia && permitido ? `/analytics/receitas/composicao${filtersQuery(f)}` : null,
  );
  if (!permitido) return null;
  return (
    <Card title="De onde vem a receita da competência">
      <DataState isLoading={isLoading && !data} error={error}>
        {data && (
          <div className="space-y-3 text-sm">
            <p className="text-slate-700">
              Receita usada no indicador: <b>{fmtBRLCompact(data.receita_operadora.valor)}</b> —{" "}
              {FONTE[data.receita_operadora.fonte] ?? data.receita_operadora.fonte}
              {data.receita_operadora.status === "divergente" && (
                <span className="ml-2 rounded bg-rose-50 px-1.5 py-0.5 text-xs text-rose-700">divergente — sinistralidade não publicada</span>
              )}
            </p>
            {data.receita_reconhecida.niveis.length > 0 && (
              <table className="w-full text-xs">
                <tbody>
                  {data.receita_reconhecida.niveis.map((n) => (
                    <tr key={n.nivel} className="border-b border-slate-50">
                      <td className="py-1 text-slate-600">{n.rotulo}</td>
                      <td className="py-1 text-right tabular-nums">{fmtBRLCompact(n.valor)}</td>
                      <td className="py-1 text-right tabular-nums text-slate-400">
                        {n.participacao != null ? fmtPct(n.participacao * 100, 0) : ""}
                      </td>
                      <td className="py-1 pl-2 text-right text-[11px] text-slate-400">
                        {n.entra_na_sinistralidade_contratual ? "entra na sinistralidade de contrato" : "fica fora dos contratos"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <div className="grid grid-cols-3 gap-2 text-xs">
              <div className="rounded-md border border-slate-200 p-2">
                <div className="text-slate-400">Reconhecida (competência de reconhecimento)</div>
                <div className="font-semibold">{fmtBRLCompact(data.receita_reconhecida.total)}</div>
              </div>
              <div className="rounded-md border border-slate-200 p-2">
                <div className="text-slate-400">Faturado (competência de faturamento)</div>
                <div className="font-semibold">{fmtBRLCompact(data.faturamento.total)}</div>
              </div>
              <div className="rounded-md border border-slate-200 p-2">
                <div className="text-slate-400">Recebido (competência de recebimento)</div>
                <div className="font-semibold">{fmtBRLCompact(data.recebimento.total)}</div>
              </div>
            </div>
            {data.avisos.map((a, i) => (
              <p key={i} className="text-[11px] text-slate-500">{a}</p>
            ))}
          </div>
        )}
      </DataState>
    </Card>
  );
}
