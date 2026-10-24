auth_wire_request() {
    local auth_operation auth_key auth_bus auth_file auth_json auth_length
    auth_operation=$1 auth_key=$2 auth_bus=$3 auth_file=$4
    if [ "$auth_operation" = catalog ]; then auth_json='{"schema_version":1,"op":"catalog"}'; else
        auth_json=$(jq -nc --arg key "$auth_key" '{schema_version:1,op:"import",device_key:$key}')
    fi
    auth_length=${#auth_json}
    [ "$auth_length" -gt 0 ] && [ "$auth_length" -lt 256 ]
    { printf '\000\000\000'; printf "\\$(printf '%03o' "$auth_length")"; printf '%s' "$auth_json"
      if [ "$auth_operation" = catalog ]; then printf '\001\021\200\005\000\000\000\000'; else
          printf '\001\021\200\003\000\000\000\000'; printf '%-32s' "$auth_bus" | tr ' ' '\000'
      fi
    } > "$auth_file"
}
auth_wire_request catalog ignored ignored "$1"
