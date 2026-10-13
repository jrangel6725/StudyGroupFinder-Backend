# Study Group Finder backend

## Setup

1. Run `study_group_finder_schema.sql` and then `study_group_finder_sample_data.sql` against MySQL 8.0.
2. Copy `backend/.env.example` to `backend/.env` and fill in your local credentials.
3. From the `backend` directory, install dependencies and start Flask:

```bash
python -m pip install -r requirements.txt
python app.py
```

The first connection check is `GET http://127.0.0.1:5000/api/health`, and the course test route is `GET http://127.0.0.1:5000/api/courses`.

## SQL assumptions

The backend matches the provided schema and uses these columns:

- `students(student_id, student_email, password_hash, first_name, last_name, date_of_birth, gender, major, phone_number)`
- `courses(course_id, course_code, course_name)`
- `study_groups(group_id, course_id, creator_id, leader_id, group_name, description, meeting_format, meeting_location, maximum_capacity)`
- `group_members(group_id, student_id)`

Signup requires `first_name`, `last_name`, `email`, `password`, `date_of_birth`, and `gender`. `major` and `phone_number` are optional. Group creation requires `course_id`, `group_name`, `meeting_format`, and `maximum_capacity`; it automatically sets both `creator_id` and `leader_id` to the logged-in student.

If the supplied schema uses different names, update the SQL column names in `app.py` to match it.

## Main routes

- `GET /api/health`
- `GET /api/courses`
- `POST /api/auth/signup`
- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/auth/me`
- `GET /api/groups?course_id=...`
- `POST /api/groups`
- `POST /api/groups/<group_id>/join`

Passwords are stored with Werkzeug hashes, user input is passed through parameterized SQL values, and group creation/joining use transactions. Joining locks the group row before checking capacity.
