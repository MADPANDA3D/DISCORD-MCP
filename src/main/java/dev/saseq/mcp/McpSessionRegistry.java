package dev.saseq.mcp;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

@Component
public class McpSessionRegistry {
    private static final Logger logger = LoggerFactory.getLogger(McpSessionRegistry.class);

    private final Map<String, SseEmitter> sessions = new ConcurrentHashMap<>();

    public SseEmitter register(String sessionId, long timeoutMs) {
        SseEmitter emitter = new SseEmitter(timeoutMs);
        sessions.put(sessionId, emitter);

        emitter.onCompletion(() -> sessions.remove(sessionId));
        emitter.onTimeout(() -> sessions.remove(sessionId));
        emitter.onError((ex) -> sessions.remove(sessionId));

        return emitter;
    }

    public boolean contains(String sessionId) {
        return sessions.containsKey(sessionId);
    }

    public void sendEndpoint(String sessionId, String endpoint) {
        sendEvent(sessionId, "endpoint", endpoint, MediaType.TEXT_PLAIN);
    }

    public void sendMessage(String sessionId, Object payload) {
        sendEvent(sessionId, "message", payload, MediaType.APPLICATION_JSON);
    }

    private void sendEvent(String sessionId, String event, Object payload, MediaType mediaType) {
        SseEmitter emitter = sessions.get(sessionId);
        if (emitter == null) {
            return;
        }
        try {
            emitter.send(SseEmitter.event().name(event).data(payload, mediaType));
        } catch (IOException ex) {
            logger.warn("Failed to send SSE event {} for session {}", event, sessionId, ex);
            sessions.remove(sessionId);
        }
    }
}
