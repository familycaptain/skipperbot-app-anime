-- Drop the hardcoded user_id default. Every caller (REST, MCP tools, pop-out
-- page) now passes the logged-in user explicitly. Anything that forgets fails
-- loudly instead of silently writing as 'user'.

ALTER TABLE anime_watch_history ALTER COLUMN user_id DROP DEFAULT;
