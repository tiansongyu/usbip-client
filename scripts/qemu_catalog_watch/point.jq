# A saved projection from the last complete matching batch; never apply partials.
. as $rows |
[range(0;length) as $i | $rows[$i] |
 select(.event=="catalog_begin" and .schema_version==1) as $head |
 [range($i+1;($rows|length)) as $j | $rows[$j] |
  select(.event=="catalog_end" and .schema_version==1 and .instance==$head.instance and .revision==$head.revision and .device_count==$head.device_count and .change_count==$head.change_count) |
  {index:$j,row:.}] | first as $tail |
 select($tail!=null) |
 ($rows[($i+1):$tail.index]) as $middle |
 select(all($middle[]; .schema_version==1 and .instance==$head.instance and .revision==$head.revision)) |
 ([$middle[]|select(.event=="catalog_device")|.catalog]) as $devices |
 ([$middle[]|select(.event=="catalog_change")|.change]) as $changes |
 select(($devices|length)==($head.device_count//0) and ($changes|length)==$head.change_count and ($middle|length)==(($devices|length)+($changes|length))) |
 select($head.state==$state and ($head.device_count//-1)==$count and $head.revision>$after) |
 {begin_index:$i,end_index:$tail.index,head:$head,devices:$devices,changes:$changes}] |
 last // empty
