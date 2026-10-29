"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { fmtBRLCompact, fmtCompetenciaCurta, fmtPct } from "@/lib/format";

const AXIS = { fontSize: 11, fill: "#64748b" };

export function EvolutionChart({
  serie,
  competenciaAtual,
}: {
  serie: { competencia: string; sinistralidade: number; acumulado_12m: number | null }[];
  competenciaAtual?: string | null;
}) {
  const data = serie.map((s) => ({
    mes: fmtCompetenciaCurta(s.competencia),
    iso: s.competencia,
    sinistralidade: s.sinistralidade,
    acumulado: s.acumulado_12m,
  }));
  return (
    <div className="h-64 w-full">
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -12 }}>
          <CartesianGrid stroke="#f1f5f9" vertical={false} />
          <XAxis dataKey="mes" tick={AXIS} interval="preserveStartEnd" minTickGap={20} />
          <YAxis tick={AXIS} width={48} tickFormatter={(v: number) => `${Math.round(v)}%`} domain={["dataMin - 4", "dataMax + 4"]} />
          <Tooltip
            formatter={(v: number, n) => [fmtPct(v), n === "sinistralidade" ? "Sinistralidade" : "Acum. 12m"]}
            labelClassName="text-xs"
            contentStyle={{ fontSize: 12, borderRadius: 8 }}
          />
          {competenciaAtual && (
            <ReferenceLine
              x={fmtCompetenciaCurta(`${competenciaAtual}-01`)}
              stroke="#94a3b8"
              strokeDasharray="3 3"
            />
          )}
          <Line
            type="monotone"
            dataKey="sinistralidade"
            stroke="#d3a63e"
            strokeWidth={2.5}
            dot={false}
            activeDot={{ r: 4 }}
          />
          <Line
            type="monotone"
            dataKey="acumulado"
            stroke="#7d95b5"
            strokeWidth={1.5}
            strokeDasharray="4 3"
            dot={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function WaterfallChart({
  fatores,
  onSelect,
}: {
  fatores: { chave: string; categoria: string; impacto_financeiro: number }[];
  onSelect?: (chave: string) => void;
}) {
  const data = fatores
    .slice(0, 10)
    .map((f) => ({ ...f, nome: f.categoria.length > 22 ? f.categoria.slice(0, 21) + "…" : f.categoria }));
  return (
    <div style={{ height: Math.max(160, data.length * 34) }} className="w-full">
      <ResponsiveContainer>
        <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, bottom: 4, left: 8 }}>
          <CartesianGrid stroke="#f1f5f9" horizontal={false} />
          <XAxis type="number" tick={AXIS} tickFormatter={(v) => fmtBRLCompact(v)} />
          <YAxis type="category" dataKey="nome" tick={{ ...AXIS, fontSize: 11 }} width={140} />
          <Tooltip formatter={(v: number) => fmtBRLCompact(v)} contentStyle={{ fontSize: 12, borderRadius: 8 }} />
          <ReferenceLine x={0} stroke="#cbd5e1" />
          <Bar
            dataKey="impacto_financeiro"
            radius={3}
            cursor={onSelect ? "pointer" : undefined}
            onClick={(d: unknown) => {
              const chave = (d as { payload?: { chave?: string } })?.payload?.chave;
              if (chave) onSelect?.(chave);
            }}
          >
            {data.map((d) => (
              <Cell key={d.chave} fill={d.impacto_financeiro >= 0 ? "#e11d48" : "#059669"} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function MiniSeries({
  serie,
  dataKey,
  color = "#22406a",
  format = "brl",
}: {
  serie: { competencia: string; [k: string]: number | string }[];
  dataKey: string;
  color?: string;
  format?: "brl" | "num" | "pct";
}) {
  const data = serie.map((s) => ({ mes: fmtCompetenciaCurta(s.competencia), v: s[dataKey] as number }));
  const fmt =
    format === "brl" ? (v: number) => fmtBRLCompact(v) : format === "pct" ? (v: number) => fmtPct(v) : (v: number) => String(v);
  return (
    <div className="h-40 w-full">
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: -8 }}>
          <CartesianGrid stroke="#f1f5f9" vertical={false} />
          <XAxis dataKey="mes" tick={AXIS} minTickGap={16} />
          <YAxis tick={AXIS} width={52} tickFormatter={fmt} />
          <Tooltip formatter={(v: number) => fmt(v)} contentStyle={{ fontSize: 12, borderRadius: 8 }} />
          <Line type="monotone" dataKey="v" stroke={color} strokeWidth={2} dot={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
