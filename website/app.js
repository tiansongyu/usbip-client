"use strict";

const tabList = document.querySelector(".backend-tabs");
const tabs = [...tabList.querySelectorAll("[data-tab]")];
const panels = [...document.querySelectorAll("[data-panel]")];

function panelTab(hash) {
  let id;
  try {
    id = decodeURIComponent(hash.slice(1));
  } catch {
    return undefined;
  }
  return tabs.find((tab) => `panel-${tab.dataset.tab}` === id);
}

function tabFromLocation() {
  const url = new URL(window.location.href);
  return panelTab(url.hash)
    ?? tabs.find((tab) => tab.dataset.tab === url.searchParams.get("backend"))
    ?? tabs[0];
}

function selectTab(tab, focus = false) {
  tabs.forEach((item) => {
    const selected = item === tab;
    item.setAttribute("aria-selected", String(selected));
    item.tabIndex = selected ? 0 : -1;
  });
  panels.forEach((panel) => { panel.hidden = panel.dataset.panel !== tab.dataset.tab; });
  if (focus) tab.focus({ preventScroll: true });
}

function updateTabUrl(tab, replace) {
  const url = new URL(window.location.href);
  url.searchParams.set("backend", tab.dataset.tab);
  if (panelTab(url.hash)) url.hash = `panel-${tab.dataset.tab}`;
  if (url.href !== window.location.href) {
    // History updates do not scroll to the hash. Keep section anchors intact;
    // arrow-key auto-activation replaces one entry rather than flooding Back.
    window.history[replace ? "replaceState" : "pushState"](null, "", url);
  }
}

function activateTab(tab, focus = false, replace = false) {
  selectTab(tab, focus);
  updateTabUrl(tab, replace);
}

function restoreTab(restoreFocus = true) {
  const tab = tabFromLocation();
  const focusedPanel = panels.find((panel) => panel.contains(document.activeElement));
  const focusWasInTabs = tabs.includes(document.activeElement);
  selectTab(tab, restoreFocus && Boolean(focusWasInTabs || (focusedPanel && focusedPanel.dataset.panel !== tab.dataset.tab)));
  // A direct panel hash takes precedence; keep its query state consistent so
  // subsequent #status/#start navigation does not restore an older backend.
  if (panelTab(window.location.hash)) updateTabUrl(tab, true);
}

panels.forEach((panel) => {
  panel.setAttribute("role", "tabpanel");
  panel.setAttribute("aria-labelledby", `tab-${panel.dataset.panel}`);
  panel.tabIndex = 0;
});
tabs.forEach((tab, index) => {
  tab.addEventListener("click", () => activateTab(tab));
  tab.addEventListener("keydown", (event) => {
    let next;
    if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
    if (event.key === "ArrowLeft") next = (index + tabs.length - 1) % tabs.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = tabs.length - 1;
    if (next !== undefined) {
      event.preventDefault();
      activateTab(tabs[next], true, true);
    }
  });
});
restoreTab(false);
window.addEventListener("hashchange", () => restoreTab());
window.addEventListener("popstate", () => restoreTab());
tabList.hidden = false;
document.querySelector(".explore").classList.add("enhanced");

const copyButton = document.querySelector("#copy-build");
const copyStatus = document.querySelector("#copy-status");
copyButton.hidden = false;
copyButton.addEventListener("click", async () => {
  copyStatus.textContent = "Copying commands… You can also select and copy them above.";
  const text = document.querySelector("#build-command").textContent;
  try {
    await navigator.clipboard.writeText(text);
    copyStatus.textContent = "Build commands copied to the clipboard.";
  } catch {
    copyStatus.textContent = "Copy is unavailable. Select and copy the commands above.";
  }
});
