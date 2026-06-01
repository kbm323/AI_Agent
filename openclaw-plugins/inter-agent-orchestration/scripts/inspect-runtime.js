import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const pluginPath = path.resolve(__dirname, "..");
const configPath = path.join(process.env.HOME ?? "", ".openclaw", "openclaw.json");
const openclawDist = path.join(process.env.HOME ?? "", ".nvm/versions/node/v24.15.0/lib/node_modules/openclaw/dist");
const discordDist = path.join(process.env.HOME ?? "", ".openclaw/npm/node_modules/@openclaw/discord/dist");

function exists(filePath) {
  return fs.existsSync(filePath);
}

function read(filePath) {
  return fs.readFileSync(filePath, "utf8");
}

function findFileContaining(dir, needle) {
  if (!exists(dir)) return undefined;
  const stack = [dir];
  while (stack.length > 0) {
    const current = stack.pop();
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) {
        if (entry.name !== "node_modules") stack.push(full);
        continue;
      }
      if (!entry.isFile() || !/\.(?:js|d\.ts|json)$/.test(entry.name)) continue;
      try {
        if (read(full).includes(needle)) return full;
      } catch {}
    }
  }
  return undefined;
}

function status(ok, label, detail) {
  console.log(`${ok ? "OK" : "FAIL"} ${label}${detail ? ` - ${detail}` : ""}`);
  return ok;
}

let passed = true;

passed = status(exists(pluginPath), "plugin path exists", pluginPath) && passed;
passed = status(exists(path.join(pluginPath, "openclaw.plugin.json")), "plugin manifest exists") && passed;
passed = status(exists(path.join(pluginPath, "index.js")), "plugin entry module exists") && passed;

let config;
try {
  config = JSON.parse(read(configPath));
  status(true, "OpenClaw config readable", configPath);
} catch (error) {
  status(false, "OpenClaw config readable", error.message);
  passed = false;
}

if (config) {
  const loadPaths = config.plugins?.load?.paths ?? [];
  const canonicalPluginPath = fs.realpathSync(pluginPath);
  const discovered = Array.isArray(loadPaths)
    && loadPaths.some((entry) => {
      try {
        return fs.realpathSync(path.resolve(String(entry))) === canonicalPluginPath;
      } catch {
        return false;
      }
    });
  passed = status(discovered, "plugin is discoverable by OpenClaw config", `plugins.load.paths includes ${pluginPath}`) && passed;

  const entry = config.plugins?.entries?.["inter-agent-orchestration"];
  passed = status(entry?.enabled === true, "plugin config entry enabled", "plugins.entries.inter-agent-orchestration.enabled=true") && passed;
  passed = status(entry?.hooks?.allowConversationAccess === true, "conversation hook access enabled", "allowConversationAccess=true") && passed;
}

const indexText = exists(path.join(pluginPath, "index.js")) ? read(path.join(pluginPath, "index.js")) : "";
const requiredRegistrations = [
  "message_received",
  "before_prompt_build",
  "before_agent_finalize",
  "agent_turn_prepare"
];
for (const hook of requiredRegistrations) {
  passed = status(indexText.includes(`api.on("${hook}"`), "hook registration path is active", hook) && passed;
}

const hookTypesFile = findFileContaining(openclawDist, 'message_received: (event: PluginHookMessageReceivedEvent');
const commandRegistrationFile = findFileContaining(openclawDist, '"message_received"');
const dispatchFile = findFileContaining(openclawDist, "runMessageReceived(toPluginMessageReceivedEvent");
const discordDispatchFile = findFileContaining(discordDist, "dispatchInboundMessage");

const runtimeSupportsMessageHook = Boolean(hookTypesFile && commandRegistrationFile && dispatchFile);
passed = status(runtimeSupportsMessageHook, "current OpenClaw gateway runtime supports message_received hooks", hookTypesFile ?? "not found") && passed;
passed = status(Boolean(discordDispatchFile), "Discord runtime enters generic inbound dispatch path", discordDispatchFile ?? "not found") && passed;

if (!runtimeSupportsMessageHook || !discordDispatchFile) {
  console.log("local plugin orchestration is not attached to the live Discord gateway runtime");
  console.log("Minimum-change external orchestrator recommendation:");
  console.log("- Run a separate lightweight Node service using the Discord Gateway API.");
  console.log("- Listen to live Discord messages directly and detect OpenClaw+Hermes mentions.");
  console.log("- Post Hermes reviewer prompts and poll the same thread externally.");
  console.log("- Post the Final synthesis externally after reading Hermes.");
  console.log("- Preserve the current OpenClaw bot, Hermes bot, personas, roles, and Discord setup.");
  console.log("- Do not patch OpenClaw dist or node_modules.");
} else {
  console.log("Runtime verdict: local plugin hook support is present. Restart openclaw-gateway.service, send a live Discord message, then check for [IAO-LIVE] message_received logs.");
}

process.exitCode = passed ? 0 : 1;
