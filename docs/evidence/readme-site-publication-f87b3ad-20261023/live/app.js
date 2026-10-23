"use strict";

const tabList = document.querySelector(".backend-tabs");
const tabs = [...tabList.querySelectorAll("[data-tab]")];
const panels = [...document.querySelectorAll("[data-panel]")];

function selectTab(tab, focus = false) {
  tabs.forEach((item) => {
    const selected = item === tab;
    item.setAttribute("aria-selected", String(selected));
    item.tabIndex = selected ? 0 : -1;
  });
  panels.forEach((panel) => { panel.hidden = panel.dataset.panel !== tab.dataset.tab; });
  if (focus) tab.focus();
}

panels.forEach((panel) => {
  panel.setAttribute("role", "tabpanel");
  panel.setAttribute("aria-labelledby", `tab-${panel.dataset.panel}`);
  panel.tabIndex = 0;
});
tabs.forEach((tab, index) => {
  tab.addEventListener("click", () => selectTab(tab));
  tab.addEventListener("keydown", (event) => {
    let next;
    if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
    if (event.key === "ArrowLeft") next = (index + tabs.length - 1) % tabs.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = tabs.length - 1;
    if (next !== undefined) {
      event.preventDefault();
      selectTab(tabs[next], true);
    }
  });
});
selectTab(tabs[0]);
tabList.hidden = false;
document.querySelector(".explore").classList.add("enhanced");

const copyButton = document.querySelector("#copy-build");
const copyStatus = document.querySelector("#copy-status");
copyButton.hidden = false;
copyButton.addEventListener("click", async () => {
  const text = document.querySelector("#build-command").textContent;
  try {
    await navigator.clipboard.writeText(text);
    copyStatus.textContent = "Build commands copied to the clipboard.";
  } catch {
    copyStatus.textContent = "Copy is unavailable. Select and copy the commands above.";
  }
});
