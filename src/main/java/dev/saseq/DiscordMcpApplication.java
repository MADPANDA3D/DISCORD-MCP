package dev.saseq;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.WebApplicationType;

import java.util.HashMap;
import java.util.Locale;
import java.util.Map;

@SpringBootApplication
public class DiscordMcpApplication {
    public static void main(String[] args) {
        String transport = System.getenv().getOrDefault("MCP_TRANSPORT", "stdio")
                .trim()
                .toLowerCase(Locale.ROOT);

        SpringApplication app = new SpringApplication(DiscordMcpApplication.class);
        app.setWebApplicationType(transport.equals("stdio") ? WebApplicationType.NONE : WebApplicationType.SERVLET);

        Map<String, Object> defaults = new HashMap<>();
        String stdioOverride = System.getenv("MCP_STDIO");
        if (stdioOverride != null && !stdioOverride.isBlank()) {
            defaults.put("spring.ai.mcp.server.stdio", stdioOverride);
        } else if (transport.equals("sse")) {
            defaults.put("spring.ai.mcp.server.stdio", "false");
        } else {
            defaults.put("spring.ai.mcp.server.stdio", "true");
        }
        app.setDefaultProperties(defaults);

        app.run(args);
    }
}
