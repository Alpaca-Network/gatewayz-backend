-- Setup Admin User for Testing /admin/users endpoint
-- This script will help you create or find an admin user

-- OPTION 1: Find existing users and their API keys
SELECT
    u.id,
    u.username,
    u.email,
    u.role,
    u.is_active,
    ak.api_key
FROM users u
LEFT JOIN api_keys_new ak ON u.id = ak.user_id AND ak.is_primary = true
ORDER BY u.created_at DESC
LIMIT 10;

-- OPTION 2: Update an existing user to admin role
-- Replace USER_ID with the ID from the query above
-- UPDATE users SET role = 'admin' WHERE id = USER_ID;

-- OPTION 3: Find a specific user by email and get their API key
-- SELECT
--     u.id,
--     u.email,
--     u.role,
--     ak.api_key
-- FROM users u
-- LEFT JOIN api_keys_new ak ON u.id = ak.user_id AND ak.is_primary = true
-- WHERE u.email = 'your-email@example.com';

-- After running this, copy the api_key value and use it as:
-- Authorization: Bearer <api_key>
