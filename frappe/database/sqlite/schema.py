import frappe
from frappe import _
from frappe.database.schema import DBTable, get_definition
from frappe.utils.defaults import get_not_null_defaults


def get_type_affinity(declared_type: str) -> str:
	"""Return SQLite's affinity for a declared column type."""
	declared_type = declared_type.upper()
	if "INT" in declared_type:
		return "integer"
	if any(token in declared_type for token in ("CHAR", "CLOB", "TEXT")):
		return "text"
	if "BLOB" in declared_type or not declared_type:
		return "blob"
	if any(token in declared_type for token in ("REAL", "FLOA", "DOUB")):
		return "real"
	return "numeric"


def types_are_compatible(current_type: str, target_type: str) -> bool:
	"""Treat spelling-only type changes as no-ops on legacy SQLite sites."""
	return current_type.lower() == target_type.lower() or get_type_affinity(
		current_type
	) == get_type_affinity(target_type)


class SQLiteTable(DBTable):
	def create(self):
		# First prepare the basic table creation without indexes
		additional_definitions = []
		varchar_len = frappe.db.VARCHAR_LEN
		name_column = f"name varchar({varchar_len}) PRIMARY KEY"

		# columns
		column_defs = self.get_column_definitions()
		if column_defs:
			additional_definitions += column_defs

		index_defs = []  # Store index definitions separately

		# child table columns
		if self.meta.get("istable", default=0):
			additional_definitions.extend(
				[
					f"parent varchar({varchar_len})",
					f"parentfield varchar({varchar_len})",
					f"parenttype varchar({varchar_len})",
				]
			)
			index_defs.append(f"CREATE INDEX `{self.table_name}_parent_idx` ON `{self.table_name}`(parent)")
		else:
			# parent types
			index_defs.append(
				f"CREATE INDEX `{self.table_name}_creation_idx` ON `{self.table_name}`(creation)"
			)
			if self.meta.sort_field == "modified":
				index_defs.append(
					f"CREATE INDEX `{self.table_name}_modified_idx` ON `{self.table_name}`(modified)"
				)

		# creating sequence(s)
		if not self.meta.issingle and self.meta.autoname == "autoincrement":
			name_column = "name INTEGER PRIMARY KEY AUTOINCREMENT"
		elif not self.meta.issingle and self.meta.autoname == "UUID":
			name_column = "name UUID PRIMARY KEY"

		additional_definitions = ",\n".join(additional_definitions)

		# create table
		create_table_query = f"""CREATE TABLE `{self.table_name}` (
			{name_column},
			creation TIMESTAMP,
			modified TIMESTAMP,
			modified_by varchar({varchar_len}),
			owner varchar({varchar_len}),
			docstatus INTEGER NOT NULL DEFAULT 0,
			idx INTEGER NOT NULL DEFAULT 0,
			{additional_definitions})"""

		# Execute table creation
		frappe.db.sql_ddl(create_table_query)

		# Create indexes separately
		for index_query in index_defs:
			frappe.db.sql_ddl(index_query)

	def alter(self):
		for col in self.columns.values():
			current_definition = self.current_columns.get(col.fieldname.lower())
			if current_definition:
				target_type = get_definition(
					col.fieldtype,
					precision=col.precision,
					length=col.length,
					options=col.options,
				)
				if target_type and types_are_compatible(current_definition.type, target_type):
					current_definition = frappe._dict(current_definition.copy())
					current_definition.type = target_type
			col.build_for_alter_table(current_definition)

		primary_key_type = self.get_primary_key_type()

		for col in self.add_column:
			# SQLite rejects ADD COLUMN when UNIQUE is inline. The matching unique
			# index is created below after the column itself exists.
			definition = col.get_definition()
			if definition.endswith(" UNIQUE"):
				definition = definition.removesuffix(" UNIQUE")
			frappe.db.sql_ddl(f"ALTER TABLE `{self.table_name}` ADD COLUMN `{col.fieldname}` {definition}")

		if not (
			self.change_type
			or self.set_default
			or self.change_nullability
			or self.add_index
			or self.add_unique
			or self.drop_index
			or self.drop_unique
			or primary_key_type
		):
			return

		# Imported lazily because sqlite.database imports SQLiteTable from here.
		from frappe.database.sqlite.database import (
			drop_single_column_indexes,
			get_column_definition,
			get_table_indexes,
			rebuild_table,
		)

		drop_index_fields = {col.fieldname for col in self.drop_index}
		drop_unique_fields = {col.fieldname for col in self.drop_unique}
		rebuild_required = bool(
			self.change_type
			or self.set_default
			or self.change_nullability
			or self.drop_unique
			or primary_key_type
		)

		if rebuild_required:
			for col in self.change_type:
				self.validate_type_change(col)

			table_columns = frappe.db.sql(f"PRAGMA table_info(`{self.table_name}`)", as_dict=1)
			column_names = [column.name for column in table_columns]
			column_definitions = [get_column_definition(column) for column in table_columns]
			definition_positions = {name: index for index, name in enumerate(column_names)}

			for col in set(self.change_type + self.set_default + self.change_nullability):
				column_definitions[definition_positions[col.fieldname]] = (
					f"`{col.fieldname}` {col.get_definition(for_modification=True)}"
				)
			if primary_key_type:
				column_definitions[definition_positions["name"]] = f"`name` {primary_key_type}"

			existing_signatures = rebuild_table(
				self.table_name,
				column_definitions,
				column_names,
				drop_index_fields=drop_index_fields,
				drop_unique_fields=drop_unique_fields,
			)
		else:
			drop_single_column_indexes(self.table_name, drop_index_fields, unique=False)
			existing_signatures = {
				(index["unique"], index["columns"])
				for index in get_table_indexes(self.table_name)
				if not index["partial"] and index["origin"] != "pk" and index["columns"]
			}

		index_queries = [
			f"CREATE UNIQUE INDEX IF NOT EXISTS `{self.table_name}_{col.fieldname}_unique_idx` "
			f"ON `{self.table_name}` (`{col.fieldname}`)"
			for col in self.add_unique
			if (True, (col.fieldname,)) not in existing_signatures
		]
		index_queries.extend(
			f"CREATE INDEX IF NOT EXISTS `{self.table_name}_{col.fieldname}_idx` "
			f"ON `{self.table_name}` (`{col.fieldname}`)"
			for col in self.add_index
			if (False, (col.fieldname,)) not in existing_signatures
		)
		if self.meta.sort_field == "modified" and not frappe.db.get_column_index(
			self.table_name, "modified", unique=False
		):
			index_queries.append(
				f"CREATE INDEX IF NOT EXISTS `{self.table_name}_modified_idx` "
				f"ON `{self.table_name}` (`modified`)"
			)

		for query in index_queries:
			frappe.db.sql_ddl(query)

	def get_primary_key_type(self) -> str | None:
		"""Return a new declared name type when UUID naming changes."""
		autoname = self.meta.autoname
		if autoname == "autoincrement":
			return None

		current_type = frappe.db.get_column_type(self.doctype, "name")
		if autoname == "UUID" and current_type != "uuid":
			if not frappe.db.get_value(self.doctype, {}, order_by=None):
				return "uuid"
			else:
				frappe.throw(
					_("Primary key of doctype {0} can not be changed as there are existing values.").format(
						self.doctype
					)
				)

		if autoname != "UUID" and current_type == "uuid":
			return f"varchar({frappe.db.VARCHAR_LEN})"
		return None

	def validate_type_change(self, column) -> None:
		"""Reject values SQLite would silently coerce during a numeric migration."""
		if column.fieldtype not in ("Int", "Long Int", "Check", "Currency", "Float", "Percent"):
			return

		value = f"TRIM(CAST(`{column.fieldname}` AS TEXT))"
		if column.fieldtype in ("Int", "Long Int", "Check"):
			pattern = r"^[+-]?[0-9]+$"
		else:
			pattern = r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$"

		invalid = frappe.db.sql(
			f"""SELECT 1 FROM `{self.table_name}`
			WHERE `{column.fieldname}` IS NOT NULL
				AND {value} != ''
				AND regexp(%s, {value}) = 0
			LIMIT 1""",
			(pattern,),
			_skip_sqlite_transpilation=True,
		)

		if not invalid and column.fieldtype in ("Int", "Long Int"):
			is_bigint = column.fieldtype == "Long Int" or (column.length and column.length > 11)
			minimum, maximum = (-(2**63), 2**63 - 1) if is_bigint else (-(2**31), 2**31 - 1)
			invalid = frappe.db.sql(
				f"""SELECT 1 FROM `{self.table_name}`
				WHERE `{column.fieldname}` IS NOT NULL
					AND {value} != ''
					AND CAST(`{column.fieldname}` AS INTEGER) NOT BETWEEN %s AND %s
				LIMIT 1""",
				(minimum, maximum),
				_skip_sqlite_transpilation=True,
			)

		if invalid:
			frappe.throw(
				_(
					"Cannot change field type in {0}: some existing values cannot be converted to the new type"
				).format(self.doctype)
			)
