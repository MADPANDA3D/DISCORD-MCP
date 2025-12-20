package dev.saseq.mcp;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.time.Instant;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

@Component
public class McpSessionRegistry {
    private static final Logger logger = LoggerFactory.getLogger(McpSessionRegistry.class);

    private final Map<String, Session> sessions = new ConcurrentHashMap<>();

    public SseEmitter register(String sessionId, long timeoutMs) {
        Session session = sessions.compute(sessionId, (key, existing) -> {
            long expiresAt = Instant.now().toEpochMilli() + timeoutMs;
            if (existing == null) {
                return new Session(null, expiresAt);
            }
            existing.setExpiresAt(expiresAt);
            return existing;
        });

        SseEmitter emitter = new SseEmitter(timeoutMs);
        session.setEmitter(emitter);

        emitter.onCompletion(() -> detachEmitter(sessionId));
        emitter.onTimeout(() -> detachEmitter(sessionId));
        emitter.onError((ex) -> detachEmitter(sessionId));

        return emitter;
    }

    public Session getSession(String sessionId) {
        Session session = sessions.get(sessionId);
        if (session == null) {
            return null;
        }
        if (session.isExpired()) {
            sessions.remove(sessionId);
            return null;
        }
        return session;
    }

    public void refresh(Session session, long timeoutMs) {
        if (session != null) {
            session.setExpiresAt(Instant.now().toEpochMilli() + timeoutMs);
        }
    }

    public void sendEndpoint(Session session, String endpoint) {
        sendEvent(session, "endpoint", endpoint, MediaType.TEXT_PLAIN);
    }

    public void sendMessage(Session session, Object payload) {
        sendEvent(session, "message", payload, MediaType.APPLICATION_JSON);
    }

    private void sendEvent(Session session, String event, Object payload, MediaType mediaType) {
        if (session == null || session.getEmitter() == null) {
            return;
        }
        try {
            session.getEmitter().send(SseEmitter.event().name(event).data(payload, mediaType));
        } catch (IOException ex) {
            logger.warn("Failed to send SSE event {}", event, ex);
            session.setEmitter(null);
        }
    }

    private void detachEmitter(String sessionId) {
        Session session = sessions.get(sessionId);
        if (session != null) {
            session.setEmitter(null);
        }
    }

    public static class Session {
        private volatile SseEmitter emitter;
        private volatile long expiresAt;

        private Session(SseEmitter emitter, long expiresAt) {
            this.emitter = emitter;
            this.expiresAt = expiresAt;
        }

        public SseEmitter getEmitter() {
            return emitter;
        }

        public void setEmitter(SseEmitter emitter) {
            this.emitter = emitter;
        }

        public boolean isExpired() {
            return Instant.now().toEpochMilli() > expiresAt;
        }

        public void setExpiresAt(long expiresAt) {
            this.expiresAt = expiresAt;
        }
    }
}
