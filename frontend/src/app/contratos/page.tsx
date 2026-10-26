"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { Card, DataState } from "@/components/ui";
import type { ContratosLista, EmpresaResumo } from "@/lib/api";
import { filtersQuery, useFilters } from "@/lib/filters";
import { fmtBRLCompact, fmtNum, fmtPct, fmtSignedPct } from "@/lib/format";
import { useApi } from "@/lib/useApi";

type SortKey = "despesa_liquida" | "top5_share" | "vidas" | "variacao_despesa_liquida_pct";

export default function ContratosPage() {
  const f = useFilters();
  const [sort, setSort] = useState<SortKey>("despesa_liquida");
  const [empresa, setEmpresa] = useState<string>("");
  const { data, error, isLoading } = useApi<ContratosLista>(
    `/analytics/contratos${filtersQuery(f)}`,
  );
  const emp = useApi<{ itens: EmpresaResumo[] }>(`/analytics/empresas${filtersQuery(f)}`);

  const itens = useMemo(() => {
    const rows = (data?.itens ?? []).filter((c) => !empresa || String(c.empresa?.id ?? "") === empresa);
    rows.sort((a, b) => (b[sort] ?? -Infinity) - (a[sort] ?? -Infinity));
    return rows;
  }, [data, sort, empresa]);

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-lg font-semibold text-slate-900">Contract Intelligence</h1>
        <p className="text-sm text-slate-500">
          Vidas, despesa (bruta → glosas → coparticipação → líquida) e concentração por contrato.
        </p>
      </header>

      {data?.aviso && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-800">
          ⚠️ {data.aviso}
        </div>
      )}

      {(emp.data?.itens.length ?? 0) > 0 && (
        <Card title="Empresas (agrupam contratos — cada contrato conta em uma só empresa)">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-100 text-left text-xs uppercase text-slate-400">
                <th className="py-2 pr-3">Empresa</th>
                <th className="px-3">Contratos</th>
                <th className="px-3">Vidas</th>
                <th className="px-3 text-right">Despesa líquida</th>
                <th className="px-3 text-right">Receita (contratos + consolidada)</th>
                <th className="px-3 text-right">Sinistralidade</th>
              </tr>
            </thead>
            <tbody>
              {emp.data!.itens.map((e) => (
                <tr
                  key={e.id}
                  className={`cursor-pointer border-b border-slate-50 hover:bg-slate-50 ${empresa === String(e.id) ? "bg-brand-50" : ""}`}
                  onClick={() => setEmpresa(empresa === String(e.id) ? "" : String(e.id))}
                  title="filtrar os contratos desta empresa"
                >
                  <td className="py-2 pr-3 font-medium text-slate-800">{e.nome}</td>
                  <td className="px-3">{e.n_contratos}</td>
                  <td className="px-3">{fmtNum(e.vidas)}</td>
                  <td className="px-3 text-right">{fmtBRLCompact(e.despesa_liquida)}</td>
                  <td className="px-3 text-right">
                    {fmtBRLCompact(e.receita_total)}
                    {e.receita_consolidada_empresa > 0 && (
                      <div className="text-[11px] text-slate-400">
                        {fmtBRLCompact(e.receita_consolidada_empresa)} sem detalhamento por contrato
                      </div>
                    )}
                  </td>
                  <td className="px-3 text-right" title={e.motivo_sem_sinistralidade ?? undefined}>
                    {e.sinistralidade_liquida != null ? fmtPct(e.sinistralidade_liquida) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      <Card
        title={`${itens.length} contratos${empresa ? " da empresa selecionada" : ""}`}
        action={
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value as SortKey)}
            className="rounded-md border border-slate-200 px-2 py-1 text-xs text-slate-600"
          >
            <option value="despesa_liquida">ordenar por despesa líquida</option>
            <option value="top5_share">ordenar por concentração</option>
            <option value="variacao_despesa_liquida_pct">ordenar por variação</option>
            <option value="vidas">ordenar por vidas</option>
          </select>
        }
      >
        <DataState isLoading={isLoading} error={error} empty={!itens.length}>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-100 text-left text-xs uppercase text-slate-400">
                  <th className="py-2 pr-3">Contrato</th>
                  <th className="px-3">Vidas</th>
                  <th className="px-3 text-right">Despesa líquida</th>
                  <th className="px-3 text-right">Δ vs comp.</th>
                  <th className="px-3 text-right">PMPM</th>
                  <th className="px-3 text-right">Conc. (top-5)</th>
                  <th className="px-3 text-right">Alto custo</th>
                  <th className="px-3 text-right">Receita reconhecida</th>
                  <th className="px-3 text-right">Sinistralidade</th>
                </tr>
              </thead>
              <tbody>
                {itens.map((c) => (
                  <tr key={c.id_contrato} className="border-b border-slate-50 hover:bg-slate-50">
                    <td className="py-2 pr-3">
                      <Link
                        href={`/contratos/${c.id_contrato}${filtersQuery(f)}`}
                        className="font-medium text-brand-700 hover:underline"
                      >
                        {c.nome}
                      </Link>
                      <div className="text-xs text-slate-400">
                        {c.plano} · {c.tipo}
                        {c.empresa ? ` · ${c.empresa.nome}` : ""}
                      </div>
                    </td>
                    <td className="px-3">{fmtNum(c.vidas)}</td>
                    <td className="px-3 text-right">{fmtBRLCompact(c.despesa_liquida)}</td>
                    <td
                      className={`px-3 text-right ${
                        (c.variacao_despesa_liquida_pct ?? 0) > 0 ? "text-rose-600" : "text-emerald-600"
                      }`}
                    >
                      {fmtSignedPct(c.variacao_despesa_liquida_pct)}
                    </td>
                    <td className="px-3 text-right">{fmtBRLCompact(c.custo_pmpm)}</td>
                    <td className="px-3 text-right">{fmtPct(c.top5_share * 100)}</td>
                    <td className="px-3 text-right">{c.n_beneficiarios_alto_custo || "—"}</td>
                    <td className="px-3 text-right">
                      {c.sinistralidade_contratual.receita_reconhecida != null
                        ? fmtBRLCompact(c.sinistralidade_contratual.receita_reconhecida)
                        : "—"}
                    </td>
                    <td
                      className="px-3 text-right"
                      title={c.sinistralidade_contratual.motivo ?? c.sinistralidade_contratual.formula}
                    >
                      {c.sinistralidade_contratual.disponivel ? fmtPct(c.sinistralidade_contratual.valor) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </DataState>
      </Card>
    </div>
  );
}
