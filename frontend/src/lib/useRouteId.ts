"use client";

import { useParams, usePathname } from "next/navigation";

/** Placeholder usado em `generateStaticParams` das rotas `[id]` no export estático. */
export const STATIC_ID_PLACEHOLDER = "_";

/**
 * Id do segmento dinâmico `[id]` da rota atual.
 *
 * No modo standalone vem de `useParams()`. No export estático cada rota de detalhe é gerada
 * uma única vez com o placeholder `_` e o proxy (nginx) serve essa página para qualquer id;
 * nesse caso o parâmetro de build é `_` e o id real é o último segmento da URL do navegador.
 */
export function useRouteId(): string {
  const params = useParams<{ id: string }>();
  const pathname = usePathname();
  if (params?.id && params.id !== STATIC_ID_PLACEHOLDER) return params.id;
  const ultimo = (pathname ?? "").replace(/\/+$/, "").split("/").pop() ?? "";
  try {
    return decodeURIComponent(ultimo);
  } catch {
    return ultimo;
  }
}
