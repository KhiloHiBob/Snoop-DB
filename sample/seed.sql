-- Synthetic demo data (no real people). Multi-tenant style: every table carries company_id.
CREATE TABLE companies (
  id BIGSERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE employees (
  id BIGSERIAL PRIMARY KEY,
  company_id BIGINT NOT NULL REFERENCES companies(id),
  full_name TEXT NOT NULL,
  department TEXT NOT NULL,
  title TEXT NOT NULL,
  salary NUMERIC(10,2) NOT NULL,
  start_date DATE NOT NULL,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  profile JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX employees_company_idx ON employees (company_id, department);

CREATE TABLE time_off_requests (
  id BIGSERIAL PRIMARY KEY,
  company_id BIGINT NOT NULL REFERENCES companies(id),
  employee_id BIGINT NOT NULL REFERENCES employees(id),
  policy TEXT NOT NULL,
  start_date DATE NOT NULL,
  end_date DATE NOT NULL,
  status TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX time_off_company_idx ON time_off_requests (company_id, status);

INSERT INTO companies (name) VALUES ('Acme Robotics'), ('Globex Foods'), ('Initech Labs');

INSERT INTO employees (company_id, full_name, department, title, salary, start_date, active, profile)
SELECT
  1 + (g % 3),
  'Demo Person ' || g,
  (ARRAY['Engineering','Sales','Support','Finance','People'])[1 + (g % 5)],
  (ARRAY['Associate','Specialist','Lead','Manager'])[1 + (g % 4)],
  40000 + (g * 937) % 60000,
  DATE '2019-01-01' + (g * 17) % 2400,
  g % 11 <> 0,
  jsonb_build_object('office', (ARRAY['Remote','London','Tel Aviv','Berlin'])[1 + (g % 4)], 'level', 1 + g % 5)
FROM generate_series(1, 120) AS g;

INSERT INTO time_off_requests (company_id, employee_id, policy, start_date, end_date, status)
SELECT e.company_id, e.id,
  (ARRAY['Vacation','Sick','Parental'])[1 + (e.id % 3)],
  DATE '2025-01-01' + (e.id * 5) % 300,
  DATE '2025-01-01' + (e.id * 5) % 300 + 1 + (e.id % 4),
  (ARRAY['approved','pending','declined'])[1 + (e.id % 3)]
FROM employees e WHERE e.id % 2 = 0;
