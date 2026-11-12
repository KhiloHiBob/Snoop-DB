# Investigating a data question

(From the HiBob `investigation-workflow` skill.)

1. **Name the store and the ID space.** Relational DB, document DB, search index or cache? Is the
   ID in the question a company id, an employee id, an entity id or a message key?
2. **State the environment** in the answer ("Ran on **prod**"). It is provided to you; always repeat it.
3. **Evidence before conclusions.** Only state facts that appear in a result you actually saw. If rows
   were withheld from you for privacy, describe the query and what the table shows, not the values.
4. **Check the field identity.** UI label -> API field -> column / JSON path can differ; confirm
   with `describe_table` before claiming a column means something.
5. **Say what you could not check** (tunnel down, no permission, result truncated).
