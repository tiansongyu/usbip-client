. as $all
| ([$all[] | select(.event == "snapshot_end")] | last) as $tail
| if $tail == null then error("no completed batch") else . end
| ([$all[] | select(.event == "snapshot_begin" and .revision == $tail.revision and .instance == $tail.instance)] | last) as $begin
| {begin: $begin,
   devices: [$all[] | select(.event == "device" and .revision == $tail.revision and .instance == $tail.instance) | .observation],
   changes: [$all[] | select(.event == "device_change" and .revision == $tail.revision and .instance == $tail.instance) | .change],
   end: $tail}
| if .begin == null or .begin.device_count != .end.device_count or .begin.change_count != .end.change_count
     or (.devices | length) != (.begin.device_count // 0) or (.changes | length) != .begin.change_count
  then error("incomplete inventory batch") else . end
