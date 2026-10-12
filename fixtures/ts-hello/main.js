import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { connect } from "@jaah/agent-sdk";

const agent = await connect();
const ctx = await agent.task();
const { task } = ctx;
agent.log("starting", "info", { taskId: task.id });

if (!task.autonomous) {                       // Full Auto: decide yourself, never ask
  const [answer] = await agent.ask([{         // waits until the operator answers or dismisses
    question: `Say hello for task ${task.id}?`,
    header: "Confirm",
    options: [{ label: "Yes" }, { label: "No" }],
  }]);
  if (answer.status === "dismissed" || answer.selections[0] !== "Yes") {
    await agent.settle("The operator declined the hello.", "no_pr", {
      noPrReason: "no_code_needed", noPrEvidence: "Operator answered No / dismissed.",
    });
    process.exit(0);
  }
}

await agent.notify(`Hello from ${ctx.agent.display_name}!`);
agent.reportUsage("my-model-1", 120, 40);

const report = join(process.env.JAAH_REPORT_DIR, "report.html");
const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
writeFileSync(report, `<!doctype html><title>Hello</title><p>Hello, ${esc(task.title)}.</p>`);
await agent.artifact("report", report);

await agent.settle("Said hello and uploaded a report.", "no_pr", {
  noPrReason: "no_code_needed", noPrEvidence: "Hello agent: no code change requested.",
});
