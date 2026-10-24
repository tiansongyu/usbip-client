. as $all
| ([$all[] | select(.event == "snapshot_end")] | last) as $end
| if $end == null then error("no completed batch") else . end
| ([$all[] | select(.event == "snapshot_begin" and .revision == $end.revision and .instance == $end.instance)] | last) as $begin
| {begin: $begin,
   devices: [$all[] | select(.event == "device" and .revision == $end.revision and .instance == $end.instance) | .observation],
   changes: [$all[] | select(.event == "device_change" and .revision == $end.revision and .instance == $end.instance) | .change],
   end: $end}
| if .begin == null or .begin.device_count != .end.device_count or .begin.change_count != .end.change_count
     or (.devices | length) != (.begin.device_count // 0) or (.changes | length) != .begin.change_count
  then error("incomplete inventory batch") else . end
