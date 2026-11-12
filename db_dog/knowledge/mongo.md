# MongoDB / DocumentDB

Use `mongo_find` for lookups and `mongo_aggregate` for grouping.

- Discover: `list_databases(store="mongo")`, then the collections of a database, then `mongo_find` with limit 3 to see the document shape.
- Filters are JSON objects: `{"company_id": 123, "status": {"$in": ["open", "pending"]}}`.
- `mongo_aggregate` allows `$match $project $group $sort $limit $skip $count $unwind $addFields $set $unset $lookup $facet`.
  `$out`, `$merge`, `$where`, `$function` and `$accumulator` are refused.
- Put `$match` first so indexes are used; always end open-ended pipelines with `$limit`.
- Ids may be `ObjectId` or strings; if a lookup by id returns nothing, try the other form.
