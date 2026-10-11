package com.thonbecker.bibleverse.functions;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

import com.thonbecker.bibleverse.model.AboutResponse;
import java.util.Properties;
import org.junit.jupiter.api.Test;
import org.springframework.boot.info.BuildProperties;

class AboutHandlerTests {

    @Test
    void returnsBuildDetails() {
        Properties properties = new Properties();
        properties.setProperty("name", "Bible Verse");
        properties.setProperty("version", "2.0.0");
        properties.setProperty("time", "2026-01-15T10:30:00Z");

        AboutResponse response = new AboutHandler(new BuildProperties(properties)).get();

        assertEquals("healthy", response.getStatus());
        assertEquals("Bible Verse", response.getPackageName());
        assertEquals("2.0.0", response.getVersion());
        assertEquals("2026-01-15T10:30:00Z", response.getTime());
    }

    @Test
    void omitsTimeWhenBuildInfoHasNone() {
        Properties properties = new Properties();
        properties.setProperty("name", "Bible Verse");
        properties.setProperty("version", "2.0.0");

        AboutResponse response = new AboutHandler(new BuildProperties(properties)).get();

        assertNull(response.getTime());
    }
}
