import pytest

from sqlloop import ReadOnlySandbox, SqlError, build_db, fingerprint


@pytest.fixture()
def db():
    return build_db(0)


@pytest.fixture()
def sandbox(db):
    return ReadOnlySandbox(db)


def test_a_select_runs_and_returns_rows(sandbox):
    r = sandbox.run("SELECT COUNT(*) FROM invoices")
    assert r.rows == [(300,)] and r.columns == ["COUNT(*)"]


def test_trailing_semicolon_and_whitespace_are_fine(sandbox):
    assert sandbox.run("  SELECT 1 ;  ").rows == [(1,)]


def test_a_semicolon_inside_a_string_literal_is_not_a_second_statement(sandbox):
    assert sandbox.run("SELECT ';' AS s").rows == [(";",)]


WRITES = [
    "DELETE FROM invoices WHERE status = 'paid'",
    "UPDATE invoices SET amount = 0",
    "INSERT INTO customers VALUES (999, 'x', 'north')",
    "DROP TABLE payments",
    "CREATE TABLE t (x)",
    "ALTER TABLE invoices ADD COLUMN x TEXT",
    "WITH x AS (SELECT 1) DELETE FROM invoices",
    "/**/DROP/**/TABLE payments",
    "dElEtE fRoM invoices",
    "PRAGMA writable_schema = 1",
    "ATTACH DATABASE '/tmp/x.db' AS x",
    "VACUUM",
    "REPLACE INTO customers VALUES (1, 'x', 'north')",
    "CREATE TRIGGER t AFTER INSERT ON invoices BEGIN DELETE FROM payments; END",
    "SELECT load_extension('x')",
]


@pytest.mark.parametrize("sql", WRITES)
def test_every_kind_of_write_is_refused_and_nothing_changes(db, sandbox, sql):
    before = fingerprint(db)
    with pytest.raises(SqlError) as e:
        sandbox.run(sql)
    # Two independent defences: the authorizer denies the operation, and a statement containing ';'
    # (a trigger body, say) is rejected up front. Either way it is refused.
    assert e.value.kind in ("forbidden", "multiple_statements")
    if "TRIGGER" not in sql:
        assert e.value.kind == "forbidden"
    assert fingerprint(db) == before


def test_the_schema_is_untouched_after_every_attack(db, sandbox):
    tables_before = db.execute("SELECT name FROM sqlite_master ORDER BY name").fetchall()
    for sql in WRITES:
        with pytest.raises(SqlError):
            sandbox.run(sql)
    assert db.execute("SELECT name FROM sqlite_master ORDER BY name").fetchall() == tables_before


@pytest.mark.parametrize("sql", ["SELECT 1; DROP TABLE payments", "SELECT 1; SELECT 2"])
def test_a_second_statement_is_rejected_before_anything_runs(db, sandbox, sql):
    with pytest.raises(SqlError) as e:
        sandbox.run(sql)
    assert e.value.kind == "multiple_statements"


def test_a_runaway_recursive_query_is_stopped(sandbox):
    with pytest.raises(SqlError) as e:
        sandbox.run("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c) SELECT count(*) FROM c")
    assert e.value.kind == "timeout"


def test_a_huge_cartesian_product_is_stopped(sandbox):
    with pytest.raises(SqlError) as e:
        sandbox.run("SELECT COUNT(*) FROM invoices, payments, customers")
    assert e.value.kind == "timeout"


def test_an_unbounded_result_is_capped(sandbox):
    with pytest.raises(SqlError) as e:
        sandbox.run("SELECT * FROM invoices, payments")
    assert e.value.kind == "too_many_rows"
    assert ReadOnlySandbox(sandbox.conn, max_rows=500).run("SELECT * FROM invoices LIMIT 400").rows


def test_the_step_limit_is_configurable(db):
    with pytest.raises(SqlError) as e:
        ReadOnlySandbox(db, max_steps=1000).run("SELECT COUNT(*) FROM invoices a, invoices b")
    assert e.value.kind == "timeout"


@pytest.mark.parametrize(
    "sql,kind",
    [("SELECT amnt FROM invoices", "no_such_column"), ("SELECT * FROM invoice", "no_such_table"),
     ("SELEC 1", "syntax"), ("", "syntax"), (";", "syntax")],
)
def test_errors_are_classified_so_the_next_attempt_can_use_them(sandbox, sql, kind):
    with pytest.raises(SqlError) as e:
        sandbox.run(sql)
    assert e.value.kind == kind


def test_the_sandbox_can_be_used_again_after_an_error_or_an_attack(sandbox):
    for sql in ("DROP TABLE payments", "SELEC", "SELECT * FROM invoice"):
        with pytest.raises(SqlError):
            sandbox.run(sql)
    assert sandbox.run("SELECT COUNT(*) FROM payments").rows[0][0] > 0
