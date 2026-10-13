-- Forward identity checks; runtime authorization remains on every service read.
CREATE TRIGGER assistant_conversation_refs_guard BEFORE INSERT ON assistant_conversations WHEN
 EXISTS(SELECT 1 FROM json_each(NEW.book_ids_json) r WHERE r.type<>'text' OR NOT EXISTS(SELECT 1 FROM books b WHERE b.id=r.value AND b.firm_id=NEW.firm_id AND b.company_id=NEW.company_id))
 OR EXISTS(SELECT 1 FROM json_each(NEW.event_ids_json) r WHERE r.type<>'text' OR NOT EXISTS(SELECT 1 FROM business_events e WHERE e.id=r.value AND e.firm_id=NEW.firm_id AND e.company_id=NEW.company_id))
 OR json_array_length(NEW.book_ids_json)<>(SELECT COUNT(DISTINCT value) FROM json_each(NEW.book_ids_json))
 OR json_array_length(NEW.event_ids_json)<>(SELECT COUNT(DISTINCT value) FROM json_each(NEW.event_ids_json))
 OR (NEW.draft_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM drafts d WHERE d.id=NEW.draft_id AND d.book_id=json_extract(NEW.book_ids_json,'$[0]')))
 BEGIN SELECT RAISE(ABORT,'Conversation references must belong to one enterprise');END;
CREATE TRIGGER assistant_message_scope_guard BEFORE INSERT ON assistant_messages WHEN NOT EXISTS(
 SELECT 1 FROM assistant_turns t WHERE t.id=NEW.turn_id AND t.conversation_id=NEW.conversation_id
 AND ((NEW.role='user' AND json(NEW.content_json)=json(json_object('question',t.question))) OR (NEW.role='assistant' AND t.status='succeeded' AND json(NEW.content_json)=json(t.reply_json))))
 BEGIN SELECT RAISE(ABORT,'Message must match an immutable turn');END;
