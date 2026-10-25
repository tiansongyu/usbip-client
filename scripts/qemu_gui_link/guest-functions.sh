# Added only to the owned disposable v2 guest; no product testing RPC.
GUI_LINK_TARGET='' GUI_LINK_QUERY=''
gui_link_read() {
    gui_js "$GUI_LINK_QUERY return {health:h,open:d.open,focused:document.activeElement===d.querySelector('summary'),rtt:d.querySelector('p').textContent,summary:d.querySelector('summary').textContent,body:document.getElementById('content').innerText};" "$1"
}
gui_link_pre() {
    local phase=$1 id=$2 before_sequence
    GUI_LINK_TARGET=$id
    gui_open "link-$phase" hid
    gui_js 'document.querySelectorAll("nav button")[2].click();return true;' /run/gui-link-connections.json
    gui_wait "any(.health[];.session_id==\"$id\" and .availability==\"live\" and .link.phase==\"open\" and .link.last_reply_sequence!=null and .link.last_round_trip_micros!=null)" "/tmp/gui-link-$phase-pre-snapshot.json"
    gui_dom "[...document.querySelectorAll('details')].some(d=>{const h=JSON.parse(d.querySelector('pre').textContent);return h.session_id==='$id'&&h.link?.last_reply_sequence!=null;})"
    GUI_LINK_QUERY="const matching=[...document.querySelectorAll('details')].filter(d=>JSON.parse(d.querySelector('pre').textContent).session_id==='$id');if(matching.length!==1)throw Error('exact health disclosure absent/ambiguous');const d=matching[0];const h=JSON.parse(d.querySelector('pre').textContent);"
    gui_js "$GUI_LINK_QUERY d.open=true;d.querySelector('summary').focus();return {health:h,open:d.open,focused:document.activeElement===d.querySelector('summary'),rtt:d.querySelector('p').textContent};" "/tmp/gui-link-$phase-expanded.json"
    jq -e '.value.open and .value.focused and .value.health.link.protocol=="framed_v2"' "/tmp/gui-link-$phase-expanded.json" >/dev/null
    before_sequence=$(jq -er '.value.health.link.last_reply_sequence' "/tmp/gui-link-$phase-expanded.json")
    gui_dom "[...document.querySelectorAll('details')].some(d=>{const h=JSON.parse(d.querySelector('pre').textContent);return h.session_id==='$id'&&h.link?.last_reply_sequence!=null&&BigInt(h.link.last_reply_sequence)>BigInt('$before_sequence');})"
    gui_link_read "/tmp/gui-link-$phase-refreshed.json"
    jq -e '.value.open and .value.focused' "/tmp/gui-link-$phase-refreshed.json" >/dev/null
    gui_http GET "/session/$GUI_SESSION/screenshot" '' "/tmp/gui-link-$phase-pre-screenshot.json"
    cat "/proc/$GUI_PID/stat" >"/tmp/gui-link-$phase-pre-stat.txt"
}
gui_link_terminal() {
    local phase=$1 id=$2
    [ "$id" = "$GUI_LINK_TARGET" ]
    gui_wait "any(.health[];.session_id==\"$id\" and .availability==\"owner_ended\" and .link.phase==\"ended\" and .link.end!=null) and any(.sessions[];.id==\"$id\" and .terminal and .cleanup==\"verified\")" "/tmp/gui-link-$phase-terminal-snapshot.json"
    gui_dom "[...document.querySelectorAll('details')].some(d=>{const h=JSON.parse(d.querySelector('pre').textContent);return h.session_id==='$id'&&h.availability==='owner_ended'&&h.link?.phase==='ended'&&h.link?.end!=null;})"
    gui_link_read "/tmp/gui-link-$phase-terminal-dom.json"
    jq -e '.value.open and .value.focused' "/tmp/gui-link-$phase-terminal-dom.json" >/dev/null
    gui_http GET "/session/$GUI_SESSION/screenshot" '' "/tmp/gui-link-$phase-terminal-screenshot.json"
    cat "/proc/$GUI_PID/stat" >"/tmp/gui-link-$phase-terminal-stat.txt"
    gui_close
}
