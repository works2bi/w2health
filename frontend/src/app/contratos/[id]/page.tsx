import ContratoDetalhePage from "./DetalheClient";

// Export estático (NEXT_OUTPUT=export): a página de detalhe é gerada uma única vez com o
// placeholder "_" e o proxy serve essa página para qualquer id — o id real é lido da URL
// (useRouteId). No modo standalone qualquer id continua renderizado sob demanda.
export function generateStaticParams() {
  return [{ id: "_" }];
}

export default function Page() {
  return <ContratoDetalhePage />;
}
