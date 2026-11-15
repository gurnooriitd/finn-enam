import { Empty } from "./ui";

export default function Research() {
  return (
    <div>
      <h1 style={{ fontSize: 20, margin: "0 0 12px", fontWeight: 700 }}>Research</h1>
      <Empty
        title="Company dossiers come in Phase 6"
        hint="Promised-vs-delivered timelines, cited Q&A and the Ask bar are on the pull side — not built yet, so this tab stays honest rather than fake."
      />
    </div>
  );
}
