package org.wpilib.command3;

import java.lang.ref.WeakReference;
import java.lang.reflect.Field;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;
import org.wpilib.hardware.hal.RobotMode;

/** Mirrors tests/conformance/asyncawait/test_trigger_scopes.py; run with tools/java_parity/run.sh. */
public class ScopeScenarios extends CommandTestBase {
  static int failures = 0;

  static void check(String name, boolean ok) {
    System.out.println((ok ? "PASS " : "FAIL ") + name);
    if (!ok) failures++;
  }

  static Command park(String name) {
    return Command.noRequirements(Coroutine::park).named(name);
  }

  static boolean collected(WeakReference<?> ref) {
    for (int i = 0; i < 5 && ref.get() != null; i++) {
      System.gc();
      try { Thread.sleep(10); } catch (InterruptedException e) { }
    }
    return ref.get() == null;
  }

  static Object field(Object o, String name) throws Exception {
    Field f = o.getClass().getDeclaredField(name);
    f.setAccessible(true);
    return f.get(o);
  }

  void reset() {
    resetRobotState();
    initScheduler();
    initOpmodeFetcher();
    initTime();
  }

  Command outer(AtomicBoolean signal, Command inner, AtomicReference<Trigger> ref) {
    return Command.noRequirements(co -> {
      var t = new Trigger(m_scheduler, signal::get);
      if (ref != null) ref.set(t);
      t.whileTrue(inner);
      co.park();
    }).named("Outer");
  }

  void whileTrueBoundInCommandIsCanceledWithTheCommand() {
    var signal = new AtomicBoolean(false);
    var inner = park("Inner");
    var outer = outer(signal, inner, null);
    m_scheduler.schedule(outer);
    m_scheduler.run();
    signal.set(true);
    m_scheduler.run();
    check("cmd-scope: inner running", m_scheduler.isRunning(inner));
    check("cmd-scope: inner is child of outer", m_scheduler.getParentOf(inner) == outer);
    m_scheduler.cancel(outer);
    check("cmd-scope: inner canceled with outer", !m_scheduler.isRunning(inner));
    m_scheduler.run();
    check("cmd-scope: inner stays canceled", !m_scheduler.isRunning(inner));
  }

  void whileTrueBoundInCommandIsCanceledWhenTheCommandCompletes() {
    var signal = new AtomicBoolean(true);
    var finish = new AtomicBoolean(false);
    var inner = park("Inner");
    var outer = Command.noRequirements(co -> {
      new Trigger(m_scheduler, signal::get).whileTrue(inner);
      co.waitUntil(finish::get);
    }).named("Outer");
    m_scheduler.schedule(outer);
    m_scheduler.run();
    m_scheduler.run();
    check("cmd-complete: inner running", m_scheduler.isRunning(inner));
    finish.set(true);
    m_scheduler.run();
    check("cmd-complete: outer done", !m_scheduler.isRunning(outer));
    check("cmd-complete: inner canceled", !m_scheduler.isRunning(inner));
  }

  void commandScopeWinsOverOpmode() throws Exception {
    m_opModeId = 7; m_opModeName = "Opmode 7";
    var signal = new AtomicBoolean(true);
    var ref = new AtomicReference<Trigger>();
    var inner = park("Inner");
    var outer = outer(signal, inner, ref);
    m_scheduler.schedule(outer);
    m_scheduler.run();
    m_scheduler.run();
    check("cmd-wins: inner running", m_scheduler.isRunning(inner));
    @SuppressWarnings("unchecked")
    var bindings = (Map<BindingType, List<Binding>>) field(ref.get(), "m_bindings");
    var binding = bindings.values().iterator().next().get(0);
    check("cmd-wins: binding scope is ForCommand(outer)",
        binding.scope() instanceof BindingScope.ForCommand fc && fc.command() == outer);
    var life = field(ref.get(), "m_lifetimeScope");
    check("cmd-wins: lifetime scope is ForCommand(outer)",
        life instanceof BindingScope.ForCommand fc2 && fc2.command() == outer);
    m_scheduler.cancel(outer);
    m_scheduler.run();
    check("cmd-wins: inner canceled while opmode still active", !m_scheduler.isRunning(inner));
  }

  void commandScopedBindingsEndWithTheOpmode() {
    m_opModeId = 7; m_opModeName = "Opmode 7";
    var signal = new AtomicBoolean(true);
    var inner = park("Inner");
    var outer = outer(signal, inner, null);
    m_scheduler.schedule(outer);
    m_scheduler.run();
    m_scheduler.run();
    check("cmd-in-opmode: both running", m_scheduler.isRunning(outer) && m_scheduler.isRunning(inner));
    m_opModeId = 8; m_opModeName = "Opmode 8";
    m_scheduler.run();
    check("cmd-in-opmode: outer canceled", !m_scheduler.isRunning(outer));
    check("cmd-in-opmode: inner canceled", !m_scheduler.isRunning(inner));
  }

  void switchingDirectlyBetweenOpmodes() throws Exception {
    m_opModeId = 1; m_opModeName = "Opmode 1";
    var signal = new AtomicBoolean(true);
    var oldCmd = park("Opmode 1 command");
    var oldTrigger = new Trigger(m_scheduler, signal::get);
    oldTrigger.whileTrue(oldCmd);
    m_scheduler.run();
    check("opmode-switch: old running", m_scheduler.isRunning(oldCmd));
    var life = field(oldTrigger, "m_lifetimeScope");
    check("opmode-switch: lifetime ForOpmode(1)",
        life instanceof BindingScope.ForOpmode fo && fo.opmodeId() == 1);
    m_opModeId = 2; m_opModeName = "Opmode 2";
    m_scheduler.run();
    check("opmode-switch: old canceled", !m_scheduler.isRunning(oldCmd));
    var ref = new WeakReference<>(oldTrigger);
    oldTrigger = null;
    check("opmode-switch: old trigger collected", collected(ref));
    var newCmd = park("Opmode 2 command");
    new Trigger(m_scheduler, signal::get).whileTrue(newCmd);
    m_scheduler.run();
    check("opmode-switch: new trigger fires on first poll", m_scheduler.isRunning(newCmd));
    m_scheduler.run();
    check("opmode-switch: new keeps running", m_scheduler.isRunning(newCmd));
  }

  void commandReusedInNextOpmode() {
    m_opModeId = 1; m_opModeName = "Opmode 1";
    var signal = new AtomicBoolean(true);
    var cmd = park("Shared");
    new Trigger(m_scheduler, signal::get).whileTrue(cmd);
    m_scheduler.run();
    check("reuse: running in opmode 1", m_scheduler.isRunning(cmd));
    m_opModeId = 2; m_opModeName = "Opmode 2";
    var trigger = new Trigger(m_scheduler, signal::get);
    trigger.whileTrue(cmd);
    m_scheduler.run();
    check("reuse: rescheduled in opmode 2", m_scheduler.isRunning(cmd));
    boolean kept = true;
    for (int i = 0; i < 3; i++) {
      m_scheduler.run();
      kept &= m_scheduler.isRunning(cmd);
    }
    check("reuse: keeps running", kept);
    signal.set(false);
    m_scheduler.run();
    check("reuse: stops on release", !m_scheduler.isRunning(cmd));
  }

  void returningToOpmodeDoesNotReviveOldTriggers() {
    m_opModeId = 1; m_opModeName = "Opmode 1";
    var signal = new AtomicBoolean(false);
    var cmd = park("Command");
    new Trigger(m_scheduler, signal::get).onTrue(cmd);
    m_scheduler.run();
    m_opModeId = 2; m_opModeName = "Opmode 2";
    m_scheduler.run();
    System.gc();
    m_opModeId = 1; m_opModeName = "Opmode 1";
    m_scheduler.run();
    signal.set(true);
    m_scheduler.run();
    check("return-to-opmode: old trigger does not fire", !m_scheduler.isRunning(cmd));
  }

  void heldSignalAcrossModeChange() {
    m_robotMode = RobotMode.TELEOPERATED;
    var signal = new AtomicBoolean(true);
    var trigger = new Trigger(m_scheduler, signal::get);
    var teleop = park("Teleop");
    trigger.onTrue(teleop);
    m_scheduler.run();
    check("held: teleop running", m_scheduler.isRunning(teleop));
    m_robotMode = RobotMode.AUTONOMOUS;
    var onTrue = park("on_true");
    var whileTrue = park("while_true");
    var ifTrue = park("if_true");
    trigger.onTrue(onTrue);
    trigger.whileTrue(whileTrue);
    trigger.ifTrue(ifTrue);
    m_scheduler.run();
    check("held: teleop canceled", !m_scheduler.isRunning(teleop));
    check("held: on_true not fired", !m_scheduler.isRunning(onTrue));
    check("held: while_true not fired", !m_scheduler.isRunning(whileTrue));
    check("held: if_true fired", m_scheduler.isRunning(ifTrue));
    signal.set(false);
    m_scheduler.run();
    signal.set(true);
    m_scheduler.run();
    check("held: on_true after re-press", m_scheduler.isRunning(onTrue));
    check("held: while_true after re-press", m_scheduler.isRunning(whileTrue));
  }

  void heldSignalFiresNewTrigger() {
    m_robotMode = RobotMode.TELEOPERATED;
    var signal = new AtomicBoolean(true);
    new Trigger(m_scheduler, signal::get).onTrue(park("Teleop"));
    m_scheduler.run();
    m_robotMode = RobotMode.AUTONOMOUS;
    var auto = park("Auto");
    new Trigger(m_scheduler, signal::get).onTrue(auto);
    m_scheduler.run();
    check("held-new: new trigger fires", m_scheduler.isRunning(auto));
  }

  public static void main(String[] args) throws Exception {
    var s = new ScopeScenarios();
    s.reset(); s.whileTrueBoundInCommandIsCanceledWithTheCommand();
    s.reset(); s.whileTrueBoundInCommandIsCanceledWhenTheCommandCompletes();
    s.reset(); s.commandScopeWinsOverOpmode();
    s.reset(); s.commandScopedBindingsEndWithTheOpmode();
    s.reset(); s.switchingDirectlyBetweenOpmodes();
    s.reset(); s.commandReusedInNextOpmode();
    s.reset(); s.returningToOpmodeDoesNotReviveOldTriggers();
    s.reset(); s.heldSignalAcrossModeChange();
    s.reset(); s.heldSignalFiresNewTrigger();
    System.out.println(failures == 0 ? "ALL PASS" : failures + " FAILED");
    System.exit(failures == 0 ? 0 : 1);
  }
}
