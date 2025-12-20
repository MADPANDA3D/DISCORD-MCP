package dev.saseq.mcp;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.util.StringUtils;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;
import org.springframework.web.servlet.support.ServletUriComponentsBuilder;

import java.util.List;
import java.util.UUID;

@RestController
@RequestMapping
public class McpSseController {
    private static final Logger logger = LoggerFactory.getLogger(McpSseController.class);

    private final McpSessionRegistry sessionRegistry;
    private final McpJsonRpcHandler jsonRpcHandler;
    private final ObjectMapper objectMapper;
    private final long timeoutMs;

    public McpSseController(McpSessionRegistry sessionRegistry,
                            McpJsonRpcHandler jsonRpcHandler,
                            ObjectMapper objectMapper,
                            @Value("${discord.mcp.sse.timeout-ms:300000}") long timeoutMs) {
        this.sessionRegistry = sessionRegistry;
        this.jsonRpcHandler = jsonRpcHandler;
        this.objectMapper = objectMapper;
        this.timeoutMs = timeoutMs;
    }

    @GetMapping(path = "/sse", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter connect(@RequestParam(name = "sessionId", required = false) String sessionId) {
        String resolvedSessionId = StringUtils.hasText(sessionId) ? sessionId : UUID.randomUUID().toString();
        SseEmitter emitter = sessionRegistry.register(resolvedSessionId, timeoutMs);

        String endpoint = ServletUriComponentsBuilder.fromCurrentContextPath()
                .path("/sse/messages/")
                .path(resolvedSessionId)
                .toUriString();

        McpSessionRegistry.Session session = sessionRegistry.getSession(resolvedSessionId);
        sessionRegistry.refresh(session, timeoutMs);
        sessionRegistry.sendEndpoint(session, endpoint);
        logger.info("SSE client connected: {}", resolvedSessionId);
        return emitter;
    }

    @PostMapping(path = {"/sse", "/sse/messages/{sessionId}"}, consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<ObjectNode> handleMessage(@RequestBody String payload,
                                                    @PathVariable(name = "sessionId", required = false) String sessionId,
                                                    @RequestParam(name = "sessionId", required = false) String sessionQuery,
                                                    @RequestHeader(name = "MCP-Session", required = false) String sessionHeader) {
        String resolvedSessionId = resolveSessionId(sessionId, sessionQuery, sessionHeader);
        McpSessionRegistry.Session session = StringUtils.hasText(resolvedSessionId)
                ? sessionRegistry.getSession(resolvedSessionId)
                : null;

        if (session == null) {
            ObjectNode error = objectMapper.createObjectNode();
            error.put("error", "Unknown or expired session");
            return ResponseEntity.status(HttpStatus.GONE).body(error);
        }

        sessionRegistry.refresh(session, timeoutMs);
        List<ObjectNode> responses = jsonRpcHandler.handlePayload(payload);
        for (ObjectNode response : responses) {
            sessionRegistry.sendMessage(session, response);
        }

        if (session.getEmitter() == null) {
            ObjectNode direct = objectMapper.createObjectNode();
            direct.put("status", "disconnected");
            direct.set("responses", objectMapper.valueToTree(responses));
            return ResponseEntity.ok(direct);
        }

        ObjectNode accepted = objectMapper.createObjectNode();
        accepted.put("status", "accepted");
        return ResponseEntity.accepted().body(accepted);
    }

    private String resolveSessionId(String pathId, String queryId, String headerId) {
        if (StringUtils.hasText(pathId)) {
            return pathId;
        }
        if (StringUtils.hasText(queryId)) {
            return queryId;
        }
        if (StringUtils.hasText(headerId)) {
            return headerId;
        }
        return null;
    }
}
