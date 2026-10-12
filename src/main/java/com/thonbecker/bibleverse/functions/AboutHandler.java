package com.thonbecker.bibleverse.functions;

import com.thonbecker.bibleverse.model.AboutResponse;
import java.time.Instant;
import java.util.function.Supplier;
import org.springframework.boot.info.BuildProperties;
import org.springframework.stereotype.Component;

@Component
public class AboutHandler implements Supplier<AboutResponse> {
    private final BuildProperties buildProperties;

    public AboutHandler(BuildProperties buildProperties) {
        this.buildProperties = buildProperties;
    }

    @Override
    public AboutResponse get() {
        Instant builtAt = buildProperties.getTime();
        return AboutResponse.builder()
                .status("healthy")
                .packageName(buildProperties.getName())
                .version(buildProperties.getVersion())
                .time(builtAt == null ? null : builtAt.toString())
                .build();
    }
}
