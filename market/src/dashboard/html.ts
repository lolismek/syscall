import { config } from "../utils/config.js";
import { getStyles } from "./styles.js";
import { getHeroHtml, getProjectsViewHtml, getAgentsViewHtml, getDetailViewHtml, getEvolutionDetailViewHtml } from "./views.js";
import { getClientScript } from "./client.js";

export function getDashboardHtml(): string {
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Syscall Market</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>S</text></svg>">
<style>
${getStyles(config.port)}
</style>
</head>
<body>
<div class="ambient-bg">
  <video autoplay muted loop playsinline preload="auto" src="/public/ambient-wave.mp4"></video>
</div>
<div class="container">
${getHeroHtml()}
${getProjectsViewHtml()}
${getAgentsViewHtml()}
${getDetailViewHtml()}
${getEvolutionDetailViewHtml()}
</div>
<script>
${getClientScript()}
</script>
</body>
</html>`;
}
