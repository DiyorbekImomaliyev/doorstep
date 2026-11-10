/**
 * One small isometric doorstep: a paper step, a wall with a maroon door, and a sealed parcel that
 * settles onto the step. Drawn with the landing page's camera and pieces (Journey3D's Box and Door), so
 * the app and the landing page share one 3D language. Decorative only: hidden from assistive tech,
 * no text, and still under reduced motion.
 */
import { Box, Door } from "./Journey3D";

export default function Doorstep3D({ className = "" }: { className?: string }) {
  return (
    <div className={`d3 ${className}`} aria-hidden>
      <div className="d3-shadow" />
      <div className="d3-world">
        {/* wall, with the door on its front face */}
        <Box x={-46} y={-44} w={92} d={12} h={58} top="#efe9e3" front="#fbf9f7" side="#e4dcd3"
          frontChildren={<><Door left={34} w={22} h={42} /><span className="d3-knob" /></>} />
        {/* the step */}
        <Box x={-46} y={-32} w={92} d={38} h={7} top="#f6f2ee" front="#e8e0d7" side="#dcd2c8" />
        {/* the parcel, sealed, settling onto the step */}
        <Box x={-4} y={-22} w={24} d={18} h={14} className="d3-parcel" top="#e3c7a4" front="#d1aa80" side="#c09469">
          <span className="d3-seal" />
        </Box>
      </div>
    </div>
  );
}
