#!/bin/sh
# Run Java parity scenarios against the real Java commandsv3 implementation.
# Each scenario mirrors a Python test (see the class comment). Needs a JDK 25 and the WPILib
# Java jars in the Gradle cache (any allwpilib Gradle build populates it).
#   ALLWPILIB=../allwpilib JAVA_HOME=~/.sdkman/candidates/java/25-tem tools/java_parity/run.sh
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
A=${ALLWPILIB:-$HERE/../../../allwpilib}
J=${JAVA_HOME:+$JAVA_HOME/bin/}
G=$HOME/.gradle/caches/modules-2/files-2.1
OUT=${TMPDIR:-/tmp}/commands3-java-parity
rm -rf "$OUT" && mkdir -p "$OUT/main" "$OUT/test" "$OUT/patched"

CP=$(find "$G"/org.wpilib.hal "$G"/org.wpilib.ntcore "$G"/org.wpilib.telemetry "$G"/org.wpilib.tunables \
  "$G"/org.wpilib.wpilibj "$G"/org.wpilib.wpimath "$G"/org.wpilib.wpiunits "$G"/org.wpilib.wpiutil \
  "$G"/us.hebi.quickbuf "$G"/org.junit.jupiter "$G"/org.junit.platform "$G"/org.opentest4j "$G"/org.apiguardian \
  -name '*.jar' ! -name '*-sources.jar' ! -name '*-javadoc.jar' | paste -sd: -)

# The button package isn't needed; CommandGenericHID is (Scheduler imports it) but references a
# constant missing from older cached jars.
sed 's/HIDDevice.MAX_DEVICES/6/' "$A/commandsv3/src/main/java/org/wpilib/command3/button/CommandGenericHID.java" \
  > "$OUT/patched/CommandGenericHID.java"
{ find "$A/wpiannotations/src/main/java" "$A/commandsv3/src/main/java" "$A/commandsv3/src/generated/main/java" \
    -name '*.java' ! -name module-info.java | grep -v /button/; echo "$OUT/patched/CommandGenericHID.java"; } > "$OUT/srcs.txt"

FLAGS="--add-exports java.base/jdk.internal.vm=ALL-UNNAMED"
"${J}javac" -nowarn -proc:none $FLAGS -d "$OUT/main" -cp "$CP" @"$OUT/srcs.txt" 2>&1 | grep -v '^warning\|^Note' || true
"${J}javac" -nowarn -proc:none $FLAGS -d "$OUT/test" -cp "$OUT/main:$CP" \
  "$A/commandsv3/src/test/java/org/wpilib/command3/CommandTestBase.java" "$HERE"/*.java
"${J}java" $FLAGS --add-opens java.base/jdk.internal.vm=ALL-UNNAMED --add-opens java.base/java.lang=ALL-UNNAMED \
  --enable-native-access=ALL-UNNAMED -cp "$OUT/test:$OUT/main:$CP" org.wpilib.command3.ScopeScenarios
